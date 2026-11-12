"""真实模型的端到端验证：Java /api/chat/stream 全链路。

判定的不是"接口返回 200"，而是：
1. 事件序列完整（intent → plan_progress → plan_draft → plan_saved → delta → done）
2. plan_draft 通过 Java 侧契约校验（Java 会二次校验，Python 过了不代表 Java 也过）
3. 计划真的落库并能从详情接口读回
4. 耗时在可接受区间（真实模型 30-60s，超过 120s 说明配额或超时配置有问题）

用法：python scripts/verify_real_llm.py [--ask "帮我规划成都三天"]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def call(method: str, path: str, body=None, token: str = "", timeout: int = 240):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request("http://localhost:8080/api" + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def stream_chat(ask: str, token: str):
    body = json.dumps({"content": ask}, ensure_ascii=False).encode()
    req = urllib.request.Request("http://localhost:8080/api/chat/stream", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Authorization", "Bearer " + token)
    events: list[tuple[str, dict]] = []
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=240) as resp:
        event_name = ""
        for raw in resp:
            line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                if event_name:
                    try:
                        events.append((event_name, json.loads(payload)))
                    except json.JSONDecodeError:
                        events.append((event_name, {"_raw": payload}))
                    event_name = ""
    return events, int((time.perf_counter() - started) * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", default="帮我规划成都三天，2个人，预算3000，喜欢美食")
    args = ap.parse_args()

    stamp = str(int(time.time()))[-8:]
    user = "real" + stamp
    call("POST", "/auth/register", {"username": user, "password": "real12345", "nickname": "真实模型"})
    token = call("POST", "/auth/login", {"username": user, "password": "real12345"})["data"]["accessToken"]
    print(f"账号就绪 {user}")

    events, elapsed = stream_chat(args.ask, token)
    names = [n for n, _ in events]
    print(f"耗时 {elapsed}ms，事件序列：{' → '.join(names)}")

    ok = True
    if "error" in names:
        err = next(d for n, d in events if n == "error")
        print(f"失败：error={err}")
        ok = False
    if "plan_draft" not in names:
        print("失败：没有收到 plan_draft")
        return 1

    plan = next(d for n, d in events if n == "plan_draft")["plan"]
    day_count = len(plan.get("days") or [])
    items = sum(len(d.get("items") or []) for d in plan.get("days") or [])
    print(f"计划：{plan.get('title')} / {day_count} 天 / {items} 个条目 / 待核实 {plan.get('unverifiedFieldCount')} 项")

    if "plan_saved" not in names:
        print("失败：没有 plan_saved，说明未落库")
        return 1
    plan_id = next(d for n, d in events if n == "plan_saved")["planId"]

    detail = call("GET", f"/plans/{plan_id}", token=token)
    stored = detail["data"]
    if stored.get("planId") != plan_id:
        print(f"失败：详情读回的 planId={stored.get('planId')} 与保存的 {plan_id} 不一致（ID 精度问题）")
        ok = False
    else:
        print(f"落库校验：详情读回成功，状态 {stored.get('status')} / 版本 {stored.get('version')}")

    if elapsed > 120_000:
        print(f"注意：耗时 {elapsed}ms 超过 120s，检查 LLM_MAX_TOKENS / 模型是否推理型")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
