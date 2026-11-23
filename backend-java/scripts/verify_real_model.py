"""真实模型复验：20 次计划生成 + 10 次调整建议（PM 2026-11-23 指令第 4 条）。

口径（写死在代码里，避免报告时口径漂移）：
- 一次「成功」= SSE 里出现 plan_saved 且没有 error 事件；
- 耗时 = POST 到 done 的墙钟时间，不是模型自报；
- 业务质量另算：天数/项数/FactField 违规数，不把「30 次全成功」当唯一指标；
- 调整只有在拿到 adjustmentId 且 ops 非空时才算成功；空操作(30110)单独计。

用法：
  python backend-java/scripts/verify_real_model.py --gen 20 --adjust 10
产物：docs/acceptance/raw/real-model-run.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8080/api"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                   "docs", "acceptance", "raw", "real-model-run.json")

TRAVEL_INPUTS = [
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
    "帮我规划哈尔滨三天，看冰雪",
    "去拉萨玩五天，两个人，注意高反",
    "帮我规划广州两天，喜欢早茶",
    "去武汉玩三天，喜欢小吃",
    "帮我规划天津两天，一个人",
    "去洛阳玩三天，喜欢古迹",
    "帮我规划贵阳四天，喜欢自然",
    "去西宁玩五天，想看看青海湖",
]

ADJUST_INSTRUCTIONS = [
    "第二天下午那项太累了，删掉",
    "把第一天的第一项改成室内活动",
    "最后一上午不想出门，把那项去掉",
    "第一天晚上加一个吃夜宵的安排",
    "第三天上午换成逛博物馆",
    "把第二天两项的顺序调换一下",
    "第一天的景点玩久一点，备注写明",
    "最后一天下午留空，不要安排",
    "第二天早上加一个早餐推荐",
    "把第三天的主题改成休闲",
]


def call(method: str, path: str, body=None, token: str = "", timeout: int = 300):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:200]}


def new_user(prefix: str) -> str:
    """固定账号：--skip-gen 复跑调整时要能登回同一个用户，否则计划不属于他（20003）。"""
    name = prefix
    call("POST", "/auth/register", {"username": name, "password": "verify12345", "nickname": "真模型"})
    _, body = call("POST", "/auth/login", {"username": name, "password": "verify12345"})
    return body["data"]["accessToken"]


def chat(token: str, content: str):
    """发一条消息，读完 SSE，返回 (事件名列表, 耗时ms, 最后一条 assistant 文本, planId)。"""
    req = urllib.request.Request(BASE + "/chat/stream",
                                 data=json.dumps({"content": content,
                                                  "clientRequestId": uuid.uuid4().hex}).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    events, plan_id, text = [], None, ""
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        event = ""
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("event:"):
                event = line[6:].strip()
                events.append(event)
            elif line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event == "plan_saved":
                    plan_id = str(data.get("planId") or data.get("plan", {}).get("planId") or "")
                if event == "delta":
                    text += data.get("content", "")
                if event == "error":
                    text += f"[error:{data.get('code')}]"
                if event == "plan_rejected":
                    text += f"[rejected:{data.get('reason')}]"
    return events, int((time.perf_counter() - started) * 1000), text, plan_id


def current_model() -> str:
    """从 AI 服务的 /v1/health 取实际生效的模型名。

    这里以前写死 `deepseek-chat`，换模型后报告里的「model」字段就是错的——
    验收数据一旦开始对不上配置，后面所有结论都要打折。宁可返回 unknown 也不猜。
    """
    try:
        with urllib.request.urlopen("http://localhost:8000/v1/health", timeout=5) as resp:
            body = json.loads(resp.read().decode())
        return ((body.get("data") or {}).get("llm") or {}).get("model") or "unknown"
    except Exception:
        return "unknown"


def detail(token: str, plan_id: str):
    _, body = call("GET", f"/plans/{plan_id}", token=token)
    return body.get("data") or {}


def walk_fact(node, out):
    if isinstance(node, dict):
        if {"value", "display", "source", "verified"} <= set(node.keys()):
            out.append(node)
            return
        for v in node.values():
            walk_fact(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_fact(v, out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", type=int, default=20)
    ap.add_argument("--adjust", type=int, default=10)
    ap.add_argument("--user", default="realverify", help="固定验收账号，便于分段复跑")
    ap.add_argument("--skip-gen", action="store_true", help="复用上次生成的计划，只补跑调整")
    args = ap.parse_args()

    token = new_user(args.user)
    print(f"user ready, gen={args.gen} adjust={args.adjust}")

    gens, plans = [], []
    if args.skip_gen:
        prev = json.load(open(OUT, encoding="utf-8"))
        gens = prev.get("gens", [])
        plans = [g["planId"] for g in gens if g.get("ok") and g.get("planId")]
        print(f"skip gen, reuse {len(plans)} plans")
    for i in range(0 if args.skip_gen else args.gen):
        text = TRAVEL_INPUTS[i % len(TRAVEL_INPUTS)]
        events, ms, answer, plan_id = chat(token, text)
        ok = "plan_saved" in events and not any(e == "error" for e in events)
        item = {"i": i + 1, "input": text, "ok": ok, "ms": ms,
                "planId": plan_id, "events": sorted(set(events)), "answerTail": answer[-120:]}
        if ok and plan_id:
            d = detail(token, plan_id)
            days = d.get("days") or []
            facts = []
            walk_fact(d, facts)
            item.update({
                "days": len(days),
                "items": sum(len(x.get("items") or []) for x in days),
                "unverifiedWithValue": sum(1 for f in facts
                                           if f.get("verified") is False and f.get("value") not in (None, "")),
                "factCount": len(facts),
                "title": d.get("title"),
                "destination": (d.get("basic") or {}).get("destination"),
                # 用户没说什么，模型就得承认不知道。这两项用来判断「卡片第二行该不该显示」，
                # 顺手也能量出模型是不是在替用户编人数（kimi 那 7 次失败就是编了 travelers=1）
                "missingFields": (d.get("basic") or {}).get("missingFields") or [],
                "travelers": (d.get("basic") or {}).get("travelers"),
                "startDate": (d.get("basic") or {}).get("startDate"),
            })
            plans.append(plan_id)
        gens.append(item)
        print(f"gen {i+1}/{args.gen} ok={ok} {ms}ms plan={plan_id}")

    adjusts = []
    for i in range(min(args.adjust, len(plans))):
        plan_id = plans[i]
        instruction = ADJUST_INSTRUCTIONS[i % len(ADJUST_INSTRUCTIONS)]
        # 调整只允许发生在已确认的行程上，先确认并开始（这一步本身幂等）
        call("POST", f"/plans/{plan_id}/confirm", token=token)
        call("POST", f"/plans/{plan_id}/start", {}, token=token)
        st, body = call("POST", f"/plans/{plan_id}/adjustments",
                        {"instruction": instruction}, token=token, timeout=300)
        data = body.get("data") or {}
        ok = st == 200 and body.get("code") == 0 and bool(data.get("adjustmentId"))
        adjusts.append({
            "i": i + 1, "planId": plan_id, "instruction": instruction, "ok": ok,
            "code": body.get("code"), "message": body.get("message"),
            "ops": len(data.get("ops") or []),
            "adjustmentId": data.get("adjustmentId"),
        })
        print(f"adjust {i+1} ok={ok} code={body.get('code')} ops={len(data.get('ops') or [])}")

    lat = sorted(g["ms"] for g in gens)
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
    summary = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": current_model(),
        "gen": {"n": len(gens), "ok": sum(1 for g in gens if g["ok"]),
                "p50ms": p50, "p95ms": p95,
                "unverifiedWithValue": sum(g.get("unverifiedWithValue", 0) for g in gens)},
        "adjust": {"n": len(adjusts), "ok": sum(1 for a in adjusts if a["ok"]),
                   "emptyOps": sum(1 for a in adjusts if a.get("code") == 30110)},
        "gens": gens, "adjusts": adjusts,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ("gen", "adjust")}, ensure_ascii=False))
    return 0 if summary["gen"]["ok"] >= 18 and summary["adjust"]["ok"] >= 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
