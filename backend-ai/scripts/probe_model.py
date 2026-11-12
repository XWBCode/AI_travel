"""真实模型联调探针。

用途：换服务商/换模型后，不改 .env 也能先在命令行确认「这个模型能不能产出可用计划」。
它走的是线上同一条路径：prompts.build_plan_messages → provider.stream_chat → extract_json → verify。

用法：
    python scripts/probe_model.py --base-url https://xxx/v1 --api-key sk-xxx --model xxx
    python scripts/probe_model.py ... --ask "帮我规划成都三天，2个人，预算3000"
    python scripts/probe_model.py ... --json-mode false      # 网关照不支持 json_object 时

判定标准只有一条：契约校验 problems 为空。模型说得再漂亮，字段不符就是不可用。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.provider import LLMError, OpenAICompatibleProvider  # noqa: E402
from app.plan.generator import run_generation  # noqa: E402
from app.plan.intent import analyze  # noqa: E402


async def run(args) -> int:
    provider = OpenAICompatibleProvider(
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout_seconds=args.timeout,
        max_tokens=args.max_tokens,
        temperature=0.7,
        json_mode=args.json_mode,
    )
    intent = analyze(args.ask)
    print(f"意图：need_plan={intent.need_plan} slots={json.dumps(intent.slots, ensure_ascii=False)}")
    if not intent.need_plan:
        print("槽位不足，跳过生成（补充信息后重试）")
        return 1

    history = [{"role": "user", "content": args.ask}]
    started = time.perf_counter()
    plan = None
    rejected: list[str] = []
    try:
        async for event, data in run_generation(provider, intent, history, trace_id="probe"):
            if event == "plan_rejected":
                rejected = data.get("errors") or []
                print(f"被拒绝 reason={data.get('reason')}")
                for p in rejected[:10]:
                    print(f"  - {p}")
            elif event == "plan_draft":
                plan = data.get("plan")
            elif event == "error":
                print(f"错误事件：{data}")
    except LLMError as e:
        print(f"调用失败：{e}")
        return 2
    elapsed = int((time.perf_counter() - started) * 1000)
    print(f"耗时：{elapsed}ms")

    if plan is None:
        print("未拿到通过校验的 plan_draft")
        return 4

    if args.dump:
        Path(args.dump).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"计划已写入 {args.dump}")

    days = plan.get("days") or []
    items = sum(len(d.get("items") or []) for d in days)
    print("契约校验通过")
    print(f"  标题：{plan.get('title')}")
    print(f"  行程：{len(days)} 天 / {items} 个条目")
    print(f"  待核实字段：{plan.get('unverifiedFieldCount')}")
    print(f"  基础信息：{json.dumps(plan.get('basic'), ensure_ascii=False)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--ask", default="帮我规划成都三天，2个人，预算3000，喜欢美食")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--json-mode", default="true", choices=["true", "false"])
    ap.add_argument("--dump", default="")
    a = ap.parse_args()
    a.json_mode = a.json_mode == "true"
    return asyncio.run(run(a))


if __name__ == "__main__":
    raise SystemExit(main())
