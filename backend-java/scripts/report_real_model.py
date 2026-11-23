"""把真实模型复验产物（docs/acceptance/raw/real-model-run.json）整理成可贴进报告的 Markdown。

为什么要单独一个脚本：这一轮之前，报告里的数字是手工从 JSON 里挑的，
挑的过程没有记录，别人复核只能重跑一遍。脚本把「怎么算的」固定下来：

- 成功率 = ok / n，不做任何"部分成功"折算；
- P50/P95 用墙钟毫秒直接排序取位，不插值（样本量本来就小，插值只会显得更精确）；
- 编造数 = `verified=false` 却带非空 `value` 的 FactField 个数；
- `missingFields` 频次用来回答「卡片的第二行该不该显示人数/出发日」。

用法：
  python backend-java/scripts/report_real_model.py
  python backend-java/scripts/report_real_model.py --json ../docs/acceptance/raw/xxx.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                       "docs", "acceptance", "raw", "real-model-run.json")


def percentile(values: list[int], ratio: float) -> int:
    """与 verify 脚本同一口径：排序后取位，不插值。"""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * ratio))]


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole}（{part * 100 / whole:.0f}%）" if whole else "0/0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=DEFAULT)
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    gens = data.get("gens") or []
    adjusts = data.get("adjusts") or []
    ok_gens = [g for g in gens if g.get("ok")]
    # 两个口径都要给：用户等的时长包含失败那几次，SLA 不能只算成功样本。
    # 之前报告只报成功样本的 P95，等于把慢的、失败的那批从统计里开除了。
    all_lat = [g.get("ms", 0) for g in gens]
    ok_lat = [g.get("ms", 0) for g in ok_gens]

    print(f"**模型**：`{data.get('model', 'unknown')}`　**时间**：{data.get('at', '?')}　"
          f"**样本**：{len(gens)} 次生成 / {len(adjusts)} 次调整")
    print()
    print("| 指标 | 值 |")
    print("|---|---|")
    print(f"| 生成成功 | {pct(len(ok_gens), len(gens))} |")
    print(f"| 端到端 P50（全部请求） | {percentile(all_lat, 0.50) / 1000:.1f}s |")
    print(f"| 端到端 P95（全部请求） | {percentile(all_lat, 0.95) / 1000:.1f}s |")
    print(f"| 端到端 P50/P95（仅成功） | {percentile(ok_lat, 0.50) / 1000:.1f}s / "
          f"{percentile(ok_lat, 0.95) / 1000:.1f}s |")
    print(f"| 最长（全部） | {max(all_lat, default=0) / 1000:.1f}s |")
    avg_items = sum(g.get("items", 0) for g in ok_gens) / len(ok_gens) if ok_gens else 0
    print(f"| 平均行程项 | {avg_items:.1f} |")
    fabricate = sum(g.get("unverifiedWithValue", 0) for g in gens)
    print(f"| 编造字段（未核实却给了值） | {fabricate} 处 |")
    ok_adjust = [a for a in adjusts if a.get("ok")]
    empty = [a for a in adjusts if a.get("code") == 30110]
    print(f"| 调整成功 | {pct(len(ok_adjust), len(adjusts))} |")
    print(f"| 调整空操作（30110） | {len(empty)} 次（未计入成功；是模型判断「没什么可改」，不是系统故障） |")

    if gens:
        print()
        print("**缺失字段频次**（`basic.missingFields`，用于判断卡片信息密度）")
        print()
        counter: Counter[str] = Counter()
        for g in gens:
            for field in g.get("missingFields") or []:
                counter[field] += 1
        print("| 字段 | 出现次数 | 占比 |")
        print("|---|---|---|")
        for field, count in counter.most_common():
            print(f"| `{field}` | {count} | {count * 100 / len(gens):.0f}% |")
        if not counter:
            print("| — | 0 | — |")

    failed_gens = [g for g in gens if not g.get("ok")]
    if failed_gens:
        print()
        print("**失败样本**")
        print()
        print("| # | 输入 | 耗时 | 末尾输出 |")
        print("|---|---|---|---|")
        for g in failed_gens:
            tail = (g.get("answerTail") or "").replace("\n", " ")[:90]
            print(f"| {g.get('i')} | {g.get('input')} | {g.get('ms', 0) / 1000:.1f}s | {tail} |")

    failed_adjust = [a for a in adjusts if not a.get("ok")]
    if failed_adjust:
        print()
        print("**调整失败样本**")
        print()
        print("| # | 指令 | code | message |")
        print("|---|---|---|---|")
        for a in failed_adjust:
            print(f"| {a.get('i')} | {a.get('instruction')} | {a.get('code')} | {a.get('message')} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
