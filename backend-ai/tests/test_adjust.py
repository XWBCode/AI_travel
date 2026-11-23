"""M2 调整建议：结构校验、mock 产出、端点契约。

运行：cd backend-ai && ./.venv/Scripts/python.exe -m pytest tests -q
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.plan.adjust import mock_adjust, validate_ops  # noqa: E402

PLAN_VIEW = {
    "title": "成都3天",
    "totalDays": 2,
    "days": [
        {"dayIndex": 1, "date": "2026-11-20", "city": "成都", "theme": "初到",
         "items": [
             {"itemId": "1001", "itemOrder": 1, "title": "宽窄巷子",
              "startTime": "09:00", "endTime": "11:00", "note": None},
             {"itemId": "1002", "itemOrder": 2, "title": "火锅",
              "startTime": "12:00", "endTime": "13:30", "note": None},
         ]},
        {"dayIndex": 2, "date": "2026-11-21", "city": "成都", "theme": "深入",
         "items": [
             {"itemId": "2001", "itemOrder": 1, "title": "都江堰",
              "startTime": "09:30", "endTime": "15:00", "note": None},
         ]},
    ],
}


def test_valid_ops_pass():
    assert validate_ops([
        {"op": "UPDATE_ITEM", "itemId": "1001", "changes": [{"field": "note", "after": "从北门进"}]},
        {"op": "REMOVE_ITEM", "itemId": "1002"},
        {"op": "ADD_ITEM", "dayIndex": 2, "title": "回酒店休息", "startTime": "15:30", "endTime": "17:00"},
        {"op": "MOVE_ITEM", "itemId": "2001", "toDayIndex": 1},
        {"op": "UPDATE_DAY", "dayIndex": 1, "changes": [{"field": "theme", "after": "慢一点"}]},
    ], PLAN_VIEW) == []


def test_unknown_item_id_rejected():
    problems = validate_ops([{"op": "REMOVE_ITEM", "itemId": "9999"}], PLAN_VIEW)
    assert problems and "不存在" in problems[0]


def test_reorder_must_list_all_items_of_the_day():
    problems = validate_ops([{"op": "REORDER_ITEM", "dayIndex": 1, "orderedItemIds": ["1001"]}], PLAN_VIEW)
    assert problems and "全部" in problems[0]


def test_field_whitelist_enforced():
    problems = validate_ops([
        {"op": "UPDATE_ITEM", "itemId": "1001", "changes": [{"field": "ticketPrice", "after": "50"}]},
    ], PLAN_VIEW)
    assert problems and "不支持修改字段" in problems[0]


def test_time_format_checked():
    problems = validate_ops([
        {"op": "UPDATE_ITEM", "itemId": "1001", "changes": [{"field": "endTime", "after": "25:00"}]},
    ], PLAN_VIEW)
    assert problems and "HH:mm" in problems[0]


def test_ops_must_be_a_list():
    assert validate_ops({"ops": []}, PLAN_VIEW) == ["调整操作必须是数组"]


def test_mock_is_deterministic_and_targets_first_item():
    first = mock_adjust(PLAN_VIEW)
    second = mock_adjust(PLAN_VIEW)
    assert first == second
    assert first["ops"][0]["itemId"] == "1001"
    # 模型给的 itemId 可能被解析成数字，mock 必须始终以字符串下发，避免前端丢精度
    assert isinstance(first["ops"][0]["itemId"], str)


def test_endpoint_returns_ops_for_mock_provider():
    with TestClient(app) as client:
        resp = client.post("/v1/plan/adjust", json={"instruction": "下午不去了", "plan": PLAN_VIEW})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["ops"]
    assert body["data"]["mock"] is True


def test_endpoint_rejects_empty_instruction():
    with TestClient(app) as client:
        resp = client.post("/v1/plan/adjust", json={"instruction": "   ", "plan": PLAN_VIEW})
    body = resp.json()
    assert body["code"] != 0
    assert "调整" in body["message"]
