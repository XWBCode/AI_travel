#!/usr/bin/env python
"""R7 探针：逐行记录 SSE 收到了什么、什么时候收到的。

为什么单独写一个探针，而不是复用 verify_real_model.py：
那份脚本只关心「最后有没有 plan_saved」，收不到也无所谓，它不会告诉你
「第 60 秒断了」还是「第 130 秒断了」。R7 要判的恰恰是时间轴：

  对照组（关心跳）：第 60 秒左右应该收到 nginx 的 504，业务没跑完
  实验组（开心跳）：每 15 秒一个 `: ping`，最大静默间隔 ≤ ~15s，最后正常 plan_saved

关键指标是 **max_gap_ms（最长静默间隔）**：它把「反代看到的空闲时长」变成了可测量的量，
比「活/死」这种二值结论更能说明心跳到底起没起作用。

用法：
  # A/B 对照（先跑 A，再关心跳跑 B）
  python scripts/r7-sse-probe.py --base http://127.0.0.1:8088/api --label nginx-heartbeat-on
  python scripts/r7-sse-probe.py --base http://127.0.0.1:8080/api --label direct-control

  # 8 并发
  python scripts/r7-sse-probe.py --concurrency 8 --out docs/acceptance/raw/r7-8concurrent.json

  # 客户端主动断开（第 40 秒关连接）
  python scripts/r7-sse-probe.py --abort-after 40 --label client-abort

产物默认写 docs/acceptance/raw/r7-<label>.json。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
import uuid

DEFAULT_CONTENT = "帮我规划成都三天，两个人，预算五千，喜欢美食和历史文化"
RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "docs", "acceptance", "raw")


def call(base: str, method: str, path: str, body=None, token: str = "", timeout: int = 60):
    u = urllib.parse.urlsplit(base + path)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    conn.request(method, u.path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "ignore")
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, {"raw": raw[:300]}


def login(base: str, user: str = "r7probe") -> str:
    call(base, "POST", "/auth/register",
         {"username": user, "password": "r7probe12345", "nickname": "R7探针"})
    status, body = call(base, "POST", "/auth/login",
                        {"username": user, "password": "r7probe12345"})
    if status != 200:
        raise SystemExit(f"登录失败：{status} {body}")
    return body["data"]["accessToken"]


def probe(base: str, token: str, content: str, abort_after: float | None = None,
          hard_timeout: float = 400.0) -> dict:
    """跑一次请求，返回时间轴。不抛异常——失败也是结果的一部分，必须记下来。"""
    u = urllib.parse.urlsplit(base + "/chat/stream")
    started = time.perf_counter()
    result: dict = {
        "base": base, "content": content, "status": None, "error": None,
        "first_byte_ms": None, "total_ms": None, "pings": 0, "events": [],
        "max_gap_ms": 0, "gaps": [], "plan_id": None, "timeline": [],
    }
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=hard_timeout)
    try:
        body = json.dumps({"content": content, "clientRequestId": uuid.uuid4().hex})
        conn.request("POST", u.path, body=body.encode(), headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": "Bearer " + token,
        })
        resp = conn.getresponse()
        result["status"] = resp.status
        last_at = started
        event_name = ""
        while True:
            if abort_after is not None and time.perf_counter() - started >= abort_after:
                result["error"] = f"客户端在 {abort_after}s 主动断开"
                break
            line = resp.readline()
            if not line:
                break
            now = time.perf_counter()
            ms = int((now - started) * 1000)
            if result["first_byte_ms"] is None:
                result["first_byte_ms"] = ms
            gap = int((now - last_at) * 1000)
            last_at = now
            text = line.decode("utf-8", "ignore").rstrip("\r\n")
            if text == "":
                continue
            if text.startswith(":"):
                result["pings"] += 1
                result["gaps"].append(gap)
                result["max_gap_ms"] = max(result["max_gap_ms"], gap)
                result["timeline"].append({"ms": ms, "kind": "ping", "text": text})
            elif text.startswith("event:"):
                event_name = text[6:].strip()
                result["events"].append(event_name)
                result["gaps"].append(gap)
                result["max_gap_ms"] = max(result["max_gap_ms"], gap)
                result["timeline"].append({"ms": ms, "kind": "event", "text": event_name})
            elif text.startswith("data:"):
                payload = text[5:].strip()
                # 只留关键字段：整份计划塞进时间轴会让产物没法看
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {}
                if event_name == "plan_saved":
                    result["plan_id"] = str(data.get("planId") or "")
                if event_name == "error":
                    result["error"] = f"error 事件 code={data.get('code')} {data.get('message', '')[:60]}"
                result["timeline"].append({"ms": ms, "kind": "data", "event": event_name,
                                           "text": payload[:120]})
    except Exception as exc:  # 连接被反代掐断、超时、reset，都走这里
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
        result["total_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def summarize(r: dict, label: str) -> dict:
    ok = r["plan_id"] is not None and not (r["error"] or "").startswith("error 事件")
    return {
        "label": label,
        "ok": ok,
        "http_status": r["status"],
        "first_byte_ms": r["first_byte_ms"],
        "total_ms": r["total_ms"],
        "pings": r["pings"],
        "max_gap_ms": r["max_gap_ms"],
        "events": r["events"],
        "plan_id": r["plan_id"],
        "error": r["error"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8088/api",
                    help="走 nginx 用 8088，直连对照用 8080")
    ap.add_argument("--label", default=None, help="产物名，默认按 base 推导")
    ap.add_argument("--content", default=DEFAULT_CONTENT)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--abort-after", type=float, default=None,
                    help="客户端在第 N 秒主动断开（验证断连后服务端是否停发心跳）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--user", default="r7probe")
    ap.add_argument("--no-timeline", action="store_true", help="产物里不写逐行时间轴")
    args = ap.parse_args()

    label = args.label or ("nginx" if ":8088" in args.base else "direct")
    out = args.out or os.path.join(RAW_DIR, f"r7-{label}.json")

    token = login(args.base, args.user)
    print(f"已登录，base={args.base} 并发={args.concurrency}")

    results: list[dict] = []
    if args.concurrency <= 1:
        results.append(probe(args.base, token, args.content, args.abort_after))
    else:
        lock = threading.Lock()

        def worker(idx: int) -> None:
            r = probe(args.base, token, args.content, args.abort_after)
            r["worker"] = idx
            with lock:
                results.append(r)
                print(f"  [{idx}] status={r['status']} total={r['total_ms']}ms "
                      f"pings={r['pings']} gap={r['max_gap_ms']}ms err={r['error']}")

        threads = [threading.Thread(target=worker, args=(i + 1,)) for i in range(args.concurrency)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    summaries = [summarize(r, f"{label}-{i + 1}" if args.concurrency > 1 else label)
                 for i, r in enumerate(sorted(results, key=lambda x: x.get("worker", 0)))]
    payload = {
        "label": label,
        "base": args.base,
        "concurrency": args.concurrency,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summaries,
        "runs": [{k: v for k, v in r.items() if k != "timeline" or not args.no_timeline}
                 for r in results],
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    for s in summaries:
        print(f"{s['label']}: ok={s['ok']} status={s['http_status']} "
              f"首字节={s['first_byte_ms']}ms 总={s['total_ms']}ms "
              f"心跳={s['pings']} 最长静默={s['max_gap_ms']}ms "
              f"events={','.join(s['events'])} err={s['error']}")
    print(f"产物：{os.path.relpath(out)}")
    return 0 if all(s["ok"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
