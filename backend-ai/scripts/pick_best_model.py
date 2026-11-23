"""交融网关模型选型：不按价格选，按"能不能稳定产出合规 JSON"选。

用户 2026-11-23 指令：「这个模型不用管便不便宜 用最好的」。

所以这个脚本不比价格，只比四件事：
  1. 合规率 —— 走项目自己的 contract.verify()，不是"看起来像 JSON"；
  2. 截断率 —— finish_reason=length 的次数（max_tokens=32768 下还截断，说明模型输出上限不够）；
  3. 延迟 —— 墙钟时间，P50/最长；
  4. 内容完整度 —— days / items / FactField 计数，和需求天数是否对得上。

用法：
  python scripts/pick_best_model.py                       # 默认候选 × 3 输入
  python scripts/pick_best_model.py --models a,b --cases 2  # 缩小范围
产物：docs/acceptance/raw/model-bench.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.prompts import build_plan_messages  # noqa: E402
from app.llm.provider import ChatMessage, OpenAICompatibleProvider  # noqa: E402
from app.plan.contract import count_fact_fields, verify  # noqa: E402
from app.plan.generator import extract_json  # noqa: E402

OUT = ROOT.parent / "docs" / "acceptance" / "raw" / "model-bench.json"

# 候选：网关里能拿到的旗舰档。flash/小参数模型放最后一档做对照，
# 是为了验证"贵的一定更好"这个假设在当前 prompt 下是否成立。
CANDIDATES = [
    "jiaorong-deepseek-v4-pro",
    "jiaorong-qwen3.5-397b-a17b",
    "jiaorong-glm-5.2",
    "jiaorong-kimi-k2.6",
    "jiaorong-deepseek-v3.2",
    "jiaorong-deepseek-v4-flash",  # 现网基线，用于回答"换掉 flash 值不值"
]

CASES = [
    ("成都三天，两个人，预算五千，喜欢美食和历史文化", 3),
    ("想去云南玩七天，喜欢自然风光", 7),
    ("帮我规划哈尔滨三天，看冰雪，带老人，节奏慢一点", 3),
]


def load_env() -> None:
    env = ROOT.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def count_items(plan: dict) -> int:
    total = 0
    for day in plan.get("days") or []:
        total += len(day.get("items") or [])
    return total


async def one(model: str, case_idx: int, text: str, want_days: int, key: str, base: str) -> dict:
    provider = OpenAICompatibleProvider(
        base_url=base, model=model, api_key=key, timeout_seconds=600,
        max_tokens=32768, temperature=0.2, json_mode=True,
    )
    messages = [ChatMessage(role=m["role"], content=m["content"])
                for m in build_plan_messages([{"role": "user", "content": text}])]
    started = time.perf_counter()
    chunks: list[str] = []
    error = ""
    try:
        async for piece in provider.stream_chat(messages, trace_id=f"bench-{model}-{case_idx}",
                                                json_mode=True):
            chunks.append(piece)
    except Exception as e:  # noqa: BLE001 —— 记录而不中断整轮
        error = f"{type(e).__name__}: {e}"
    elapsed = time.perf_counter() - started

    raw = "".join(chunks)
    finish = getattr(provider, "last_finish_reason", None)
    plan, parse_err, reason = extract_json(raw, finish)

    # 必须复刻 generator 的口径：unverifiedFieldCount 是派生统计量，服务端重算覆盖，
    # 不参与拒绝（08 文档 S-5）。少了这一步会把「模型按 prompt 固定填 0」误判成不合规。
    declared = plan.get("unverifiedFieldCount") if plan else None
    if plan:
        plan["unverifiedFieldCount"] = count_fact_fields(plan)

    violations = verify(plan) if plan else []
    days = len(plan.get("days") or []) if plan else 0
    record = {
        "model": model,
        "case": case_idx,
        "input": text,
        "elapsedMs": int(elapsed * 1000),
        "chars": len(raw),
        "finishReason": finish,
        "ok": bool(plan) and not violations and not error,
        "error": error,
        "parseError": parse_err,
        "reason": reason,
        "days": days,
        "wantDays": want_days,
        "items": count_items(plan) if plan else 0,
        "factFields": count_fact_fields(plan) if plan else 0,
        "declaredCount": declared,
        "violations": violations[:5],
    }
    # 原始响应落盘：口径以后若要调整（比如再加一条一致性规则），可以离线重判，不用重烧额度
    raws = OUT.parent / "model-bench-raw"
    raws.mkdir(parents=True, exist_ok=True)
    (raws / f"{model}-case{case_idx}.json").write_text(
        json.dumps({"raw": raw, "finishReason": finish}, ensure_ascii=False), encoding="utf-8")
    print(f"  {model:34s} case{case_idx} {'OK ' if record['ok'] else 'FAIL'}"
          f" {record['elapsedMs']:>6}ms {record['chars']:>6}c"
          f" finish={finish} days={days}/{want_days} items={record['items']}"
          f"{' ' + error[:60] if error else ''}"
          f"{' | ' + parse_err[:60] if parse_err else ''}"
          f"{' | ' + str(violations[:2]) if violations else ''}", flush=True)
    return record


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(CANDIDATES))
    ap.add_argument("--cases", type=int, default=len(CASES))
    ap.add_argument("--key", default="")
    args = ap.parse_args()

    load_env()
    key = args.key or os.environ.get("LLM_API_KEY", "")
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    if not key or not base:
        print("缺少 LLM_API_KEY / LLM_BASE_URL")
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    records: list[dict] = []
    # 串行：网关按 key 限速，并发会把"模型不行"和"我被限流了"混在一起
    for model in models:
        print(f"\n== {model}")
        for idx, (text, want) in enumerate(CASES[:args.cases]):
            records.append(await one(model, idx, text, want, key, base))

    summary = []
    for model in models:
        rows = [r for r in records if r["model"] == model]
        times = sorted(r["elapsedMs"] for r in rows)
        summary.append({
            "model": model,
            "runs": len(rows),
            "ok": sum(1 for r in rows if r["ok"]),
            "truncated": sum(1 for r in rows if r["finishReason"] == "length"),
            "errors": sum(1 for r in rows if r["error"]),
            "p50Ms": int(statistics.median(times)),
            "maxMs": times[-1],
            "avgItems": round(statistics.mean(r["items"] for r in rows), 1),
            "daysMatched": sum(1 for r in rows if r["days"] == r["wantDays"]),
        })

    print("\n== 汇总（按 合规 > 少截断 > 快 排序不代表结论，看原表）")
    for s in summary:
        print(f"  {s['model']:34s} ok={s['ok']}/{s['runs']} trunc={s['truncated']}"
              f" err={s['errors']} p50={s['p50Ms']}ms max={s['maxMs']}ms"
              f" items≈{s['avgItems']} daysOK={s['daysMatched']}/{s['runs']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "records": records},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
