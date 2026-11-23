"""AC-07（结构层）：契约里的 10 组畸形样例必须全部被拒，且报错落在预期路径上。

这 10 组此前只以 JSON 文件形式存在，没有任何测试真正执行它们——
「样例在仓库里」不等于「样例在验收里」。这里把它变成可执行断言。

基线用 mock 生成器：它是确定性的，所以「改动前合法、改动后必被拒」这件事可复现。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.plan import mock
from app.plan.contract import validate_draft
from app.plan.intent import analyze

CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "travel-plan"
    / "examples"
    / "invalid_cases.json"
)


def _load() -> list[dict]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def _base_plan() -> dict:
    intent = analyze("帮我规划成都三天，两个人，喜欢美食")
    assert intent.need_plan, "基线意图必须足以生成计划"
    plan = mock.build_plan(intent)
    assert validate_draft(plan) == [], "基线计划本身必须合法，否则下面的用例没有意义"
    return plan


def _apply(node, path: list, value):
    for part in path[:-1]:
        node = node[part]
    last = path[-1]
    node[last] = value


@pytest.mark.parametrize("case", _load(), ids=[c["name"] for c in _load()])
def test_malformed_plan_is_rejected(case: dict):
    plan = copy.deepcopy(_base_plan())
    for mutation in case["mutations"]:
        _apply(plan, mutation["path"], mutation["value"])

    errors = validate_draft(plan)
    assert errors, f"{case['name']} 未被拒绝：{case['why']}"
    expect = case.get("expect", "")
    if expect:
        assert any(e.startswith(expect) for e in errors), (
            f"{case['name']} 被拒了，但报错路径不是预期的 {expect!r}，实际：{errors[:3]}"
        )


def test_case_set_is_large_enough():
    cases = _load()
    assert len(cases) >= 10
    for c in cases:
        assert c.get("mutations"), f"{c['name']} 没有变异操作"
        assert c.get("why"), f"{c['name']} 缺少 why，后人不知道为什么要拒"
