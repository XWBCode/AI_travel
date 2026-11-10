"""WP3 测试：意图判定、Mock 生成、契约校验、SSE 端到端。

运行：cd backend-ai && ./.venv/Scripts/python.exe -m pytest tests -q
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LLM_PROVIDER", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.plan.contract import (  # noqa: E402
    check_consistency,
    count_fact_fields,
    is_fact_field,
    scan_free_text,
    validate_draft,
    verify,
)
from app.plan.generator import extract_json  # noqa: E402
from app.plan.intent import analyze  # noqa: E402
from app.plan.mock import build_plan  # noqa: E402


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        name, data = "", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name:
            events.append((name, json.loads(data) if data else {}))
    return events


# ---------------- 意图判定 ----------------

def test_intent_full_request():
    intent = analyze("我想去成都玩三天，预算三千，喜欢美食")
    assert intent.scene == "plan"
    assert intent.need_plan is True
    assert intent.slots["destination"] == "成都"
    assert intent.slots["days"] == 3
    assert intent.slots["budget"] == 3000
    assert "美食" in intent.slots["interests"]
    # 信息够生成，但出发地/日期缺失仍要如实列入
    assert "originCity" in intent.missing_fields
    assert "startDate" in intent.missing_fields
    assert "budget" not in intent.missing_fields


def test_intent_general_chat():
    intent = analyze("你好，你是谁")
    assert intent.scene == "general"
    assert intent.need_plan is False
    assert intent.missing_fields == []


def test_intent_missing_days():
    intent = analyze("我想去成都")
    assert intent.scene == "plan"
    assert intent.slots["destination"] == "成都"
    assert intent.need_plan is False
    assert "days" in intent.missing_fields
    assert intent.follow_ups == ["days"]


def test_intent_chinese_date_and_days():
    intent = analyze("11月20日去杭州玩三天")
    assert intent.slots["destination"] == "杭州"
    assert intent.slots["days"] == 3
    assert intent.slots["startDate"].endswith("-11-20")
    # 只有开始日期时，结束日期由天数推出
    assert intent.slots["endDate"].endswith("-11-22")
    assert intent.need_plan is True


def test_intent_multi_city():
    intent = analyze("去成都和乐山玩四天")
    assert intent.slots["destinations"] == ["成都", "乐山"]
    assert intent.slots["destination"] == "成都"
    assert intent.slots["days"] == 4


def test_intent_origin():
    intent = analyze("从西安出发去成都玩5天")
    assert intent.slots["originCity"] == "西安"
    assert intent.slots["destination"] == "成都"
    assert intent.slots["days"] == 5


# ---------------- Mock 生成 ----------------

def test_mock_plan_is_valid():
    plan = build_plan(analyze("我想去成都玩三天，预算三千，喜欢美食"))
    assert verify(plan) == []
    assert plan["status"] == "DRAFT"
    assert plan["planId"] is None
    assert plan["basic"]["destination"] == "成都"
    assert len(plan["days"]) == 3
    assert plan["days"][-1]["accommodation"] is None
    assert plan["days"][0]["accommodation"] is not None


def test_mock_plan_fact_field_count_matches():
    plan = build_plan(analyze("去成都玩两天"))
    assert plan["unverifiedFieldCount"] == count_fact_fields(plan)
    assert plan["unverifiedFieldCount"] > 0


def test_mock_plan_no_concrete_numbers_in_text():
    plan = build_plan(analyze("去杭州玩四天，喜欢美食和历史"))
    assert scan_free_text(plan) == []


def test_mock_plan_inter_city_segment():
    plan = build_plan(analyze("去成都和乐山玩四天"))
    scopes = [seg["scope"] for day in plan["days"] for seg in day["transportSegments"]]
    assert "INTER_CITY" in scopes
    inter = next(seg for day in plan["days"] for seg in day["transportSegments"] if seg["scope"] == "INTER_CITY")
    assert inter["fromCity"] == "成都"
    assert inter["toCity"] == "乐山"
    assert inter["fromItemOrder"] is None


def test_mock_plan_multi_city_dates_aligned():
    plan = build_plan(analyze("11月20日去杭州玩两天"))
    assert [d["date"] for d in plan["days"]] == ["2026-11-20", "2026-11-21"]


# ---------------- 契约拒绝 ----------------

def test_schema_rejects_non_null_value():
    plan = build_plan(analyze("去成都玩两天"))
    plan["days"][0]["items"][0]["ticketPrice"]["value"] = 60
    assert validate_draft(plan) != []


def test_consistency_rejects_wrong_count():
    plan = build_plan(analyze("去成都玩两天"))
    plan["unverifiedFieldCount"] = 1
    problems = check_consistency(plan)
    assert any("unverifiedFieldCount" in p for p in problems)


def test_consistency_rejects_missing_field_mismatch():
    plan = build_plan(analyze("我想去成都玩三天"))
    # 原本 originCity 为 null 且已列入 missingFields，这里改成有值却不移除
    assert plan["basic"]["originCity"] is None
    plan["basic"]["originCity"] = "西安"
    problems = check_consistency(plan)
    assert any("originCity" in p for p in problems)


def test_schema_rejects_numeric_free_text():
    plan = build_plan(analyze("去成都玩两天"))
    plan["risks"] = ["景区门票 60 元"]
    assert validate_draft(plan) != []


def test_is_fact_field_strict():
    good = {"value": None, "display": "待核实", "source": "NONE", "verified": False,
            "fetchedAt": None, "expiresAt": None, "note": "x"}
    assert is_fact_field(good) is True
    assert is_fact_field({**good, "extra": 1}) is False
    assert is_fact_field({"value": None}) is False


# ---------------- JSON 提取 ----------------

def test_extract_json_from_fence():
    plan, err, reason = extract_json('```json\n{"a": 1}\n```')
    assert plan == {"a": 1}
    assert err == ""


def test_extract_json_with_leading_text():
    plan, _, _ = extract_json('以下是您的行程：\n{"a": 1}\n祝旅途愉快')
    assert plan == {"a": 1}


def test_extract_json_invalid():
    plan, err, reason = extract_json("抱歉，我无法完成")
    assert plan is None
    assert reason == "INVALID_FORMAT"


# ---------------- SSE 端到端 ----------------

def test_plan_stream_sse():
    with TestClient(app) as client:
        resp = client.post(
            "/v1/plan/stream",
            json={"messages": [{"role": "user", "content": "我想去成都玩三天，喜欢美食"}]},
            headers={"X-Trace-Id": "test-trace-1"},
        )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

    names = [name for name, _ in events]
    assert names[0] == "intent"
    assert "plan_draft" in names
    assert names[-1] == "done"

    intent_data = events[0][1]
    assert intent_data["needPlan"] is True
    assert intent_data["traceId"] == "test-trace-1"

    draft = next(data for name, data in events if name == "plan_draft")
    assert draft["mock"] is True
    assert verify(draft["plan"]) == []
    # 所有事件都要带 traceId（05 文档 §4 统一要求）
    assert all(data.get("traceId") == "test-trace-1" for _, data in events)


def test_plan_stream_skips_when_incomplete():
    with TestClient(app) as client:
        resp = client.post(
            "/v1/plan/stream",
            json={"messages": [{"role": "user", "content": "我想去成都"}]},
        )
        events = _parse_sse(resp.text)

    names = [name for name, _ in events]
    assert "plan_draft" not in names
    done = next(data for name, data in events if name == "done")
    assert done["skipped"] == "NEED_MORE_INFO"
    assert "days" in done["missingFields"]


def test_intent_endpoint():
    with TestClient(app) as client:
        resp = client.post(
            "/v1/plan/intent",
            json={"messages": [{"role": "user", "content": "从西安去成都玩三天"}]},
        )
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["needPlan"] is True
    assert body["data"]["slots"]["originCity"] == "西安"
