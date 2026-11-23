"""并发生成压测：量出 SSE 线程池与网关的实际上限（可靠性报告 R5）。

口径写死在这里，避免报告时口径漂移：

- 一次「成功」= SSE 里出现 `plan_saved` 且没有 `error` 事件；
- `10429` 是 Java 线程池满导致的拒绝（`RejectedExecutionException`），属于**我们自己的上限**；
- `50201` / `429` 来自 AI 服务或网关，属于**外部上限**——两者要分开计数，
  混在一起就分不清「该扩容」还是「该降并发」；
- 耗时是墙钟：从发请求到最后一个字节。

用法：
  python backend-java/scripts/load_test_stream.py --concurrency 8
  python backend-java/scripts/load_test_stream.py --concurrency 12 --user loadtest

注意：会真实调用模型，按并发数 × 2 分钟消耗额度。跑之前确认当前模型的超时配置
（`AI_PLAN_TIMEOUT_MS`）足够，否则长尾会被误判成失败。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
import os

BASE = "http://localhost:8080/api"
RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                       "docs", "acceptance", "raw")

INPUTS = [
    "帮我规划成都三天，两个人，预算五千，喜欢美食和历史文化",
    "我想去西安玩四天，一个人，喜欢历史",
    "帮我规划厦门三天，爱吃海鲜，想住海边",
    "去杭州玩两天，带父母，节奏慢一点",
    "想去云南玩五天，喜欢自然风光",
    "帮我规划青岛三天，两个人，预算四千",
    "去重庆玩三天，喜欢火锅和夜景",
    "帮我规划长沙两天，一个人，预算一千五",
    "想去南京玩四天，喜欢博物馆",
    "去苏州玩三天，两个人，喜欢园林",
    "帮我规划桂林四天，喜欢山水",
    "去大理玩五天，一个人，预算三千",
]


def call(method: str, path: str, body=None, token: str = ""):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def login(user: str) -> str:
    call("POST", "/auth/register", {"username": user, "password": "verify12345", "nickname": "压测"})
    _, body = call("POST", "/auth/login", {"username": user, "password": "verify12345"})
    return body["data"]["accessToken"]


def one(token: str, index: int) -> dict:
    text = INPUTS[index % len(INPUTS)]
    req = urllib.request.Request(
        BASE + "/chat/stream",
        data=json.dumps({"content": text, "clientRequestId": uuid.uuid4().hex}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    events: list[str] = []
    errors: list[int] = []
    plan_id = None
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            event = ""
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                    events.append(event)
                elif line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if event == "plan_saved":
                        plan_id = str(payload.get("planId") or "")
                    if event == "error":
                        errors.append(int(payload.get("code") or 0))
    except Exception as e:  # 连接层失败也要计数，不能当成「没发生」
        errors.append(-1)
        events.append(f"exception:{type(e).__name__}")
    return {
        "i": index + 1,
        "input": text,
        "ms": int((time.perf_counter() - started) * 1000),
        "ok": bool(plan_id) and not errors,
        "planId": plan_id,
        "errors": errors,
        "events": sorted(set(events)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--user", default="loadtest")
    args = ap.parse_args()

    token = login(args.user)
    print(f"user ready, concurrency={args.concurrency}", flush=True)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda i: one(token, i), range(args.concurrency)))
    wall = int((time.perf_counter() - started) * 1000)

    ok = [r for r in results if r["ok"]]
    rejected = [r for r in results if 10429 in r["errors"]]
    upstream = [r for r in results if any(c not in (10429, 0) for c in r["errors"])]
    lat = [r["ms"] for r in results]

    summary = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "concurrency": args.concurrency,
        "ok": len(ok),
        "rejectedByPool": len(rejected),
        "upstreamErrors": len(upstream),
        "wallMs": wall,
        "p50Ms": int(statistics.median(lat)) if lat else 0,
        "maxMs": max(lat) if lat else 0,
        "codeHistogram": {str(c): sum(1 for r in results if c in r["errors"]) for c in
                          sorted({c for r in results for c in r["errors"]})},
        "results": results,
    }
    out = os.path.join(RAW_DIR, f"load-test-{args.concurrency}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: summary[k] for k in
                      ("concurrency", "ok", "rejectedByPool", "upstreamErrors",
                       "wallMs", "p50Ms", "maxMs", "codeHistogram")},
                     ensure_ascii=False))
    print(f"产物：{out}")
    return 0 if len(ok) == args.concurrency else 1


if __name__ == "__main__":
    raise SystemExit(main())
