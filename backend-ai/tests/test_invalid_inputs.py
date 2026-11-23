"""AC-07（输入层）：畸形输入必须被安全拒绝或降级。

样例集在 contracts/travel-plan/examples/invalid_inputs.json，与 Java/Python 共用同一份，
不在这里另维护一份副本——否则两侧断言迟早漂移。

判定口径：每条样例的 asserts 全部成立才算通过。断言写的是**期望行为**，
不是把现状抄一遍；所以样例集新增条目时，先用临时脚本看实际输出，再逐条确认期望是否合理。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plan.intent import analyze_messages

CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "travel-plan"
    / "examples"
    / "invalid_inputs.json"
)


def _load() -> list[dict]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


def _get(payload: dict, path: str):
    cur = payload
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _check(payload: dict, spec: dict) -> tuple[bool, str]:
    value = _get(payload, spec["path"])
    op = spec["op"]
    expect = spec.get("value")
    if op == "eq":
        ok = value == expect
    elif op == "isNull":
        ok = value is None
    elif op == "contains":
        ok = isinstance(value, list) and expect in value
    elif op == "gte":
        ok = value is not None and value >= expect
    elif op == "lte":
        ok = value is not None and value <= expect
    else:  # pragma: no cover - 样例集写错时直接炸，不静默跳过
        raise AssertionError(f"未知断言操作符: {op}")
    return ok, f"{spec['path']} {op} {expect!r}，实际 {value!r}"


@pytest.mark.parametrize("case", _load(), ids=[c["id"] for c in _load()])
def test_invalid_input_is_rejected_safely(case: dict):
    intent = analyze_messages([{"role": "user", "content": case["input"]}])
    payload = {**intent.to_payload(), "slots": intent.slots}

    failures = []
    for spec in case["asserts"]:
        ok, msg = _check(payload, spec)
        if not ok:
            failures.append(msg)

    assert not failures, f"{case['id']}（{case['category']}）{case['input']!r}：{case['expect']}；不满足：{failures}"


def test_case_set_is_large_enough():
    """样本量本身是 AC-07 的验收指标之一，别在删样例时悄悄退化。"""
    cases = _load()
    categories = {c["category"] for c in cases}
    assert len(cases) >= 44, f"输入层畸形样例只有 {len(cases)} 组"
    assert len(categories) >= 9, f"类别只有 {len(categories)} 个"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "样例 id 重复"
    for c in cases:
        assert c["asserts"], f"{c['id']} 没有断言，等于没测"


def test_long_input_does_not_explode():
    """超长输入（20 万字符）必须在可接受时间内返回，不能 O(n²) 卡死。"""
    import time

    text = "去成都玩三天。" * 20000
    started = time.perf_counter()
    intent = analyze_messages([{"role": "user", "content": text}])
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"超长输入耗时 {elapsed:.2f}s，说明解析存在平方级开销"
    assert intent.need_plan is True
    assert intent.slots["destination"] == "成都"
