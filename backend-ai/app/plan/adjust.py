"""调整建议（R-23 / R-24）。

职责边界与生成器一致：本服务调模型、解析、做**结构级**校验，不落库、不判权限。
业务表的写入只能发生在 Java 侧，并且必须等用户点确认——AC-11 的「AI 直接写正式计划次数 = 0」
在结构上靠这条分界保证，而不是靠调用方自觉。

为什么这里也要校验一遍（Java 会再校验）：
  模型输出不合规时，在这里就拒绝可以让用户马上重说一遍，不用等一个必然失败的确认动作；
  但最终裁判始终是 Java——它才看得见库里真实的计划，也只有它能保证 itemId 的归属。
  两边的规则刻意保持同一份白名单，改动时须同步。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.llm.prompts import build_adjust_messages
from app.llm.provider import ChatMessage, LLMError, LLMProvider
from app.plan.generator import extract_json

logger = logging.getLogger("ai-travel.adjust")

ITEM_FIELDS = ("title", "startTime", "endTime", "note")
DAY_FIELDS = ("theme", "city")
MAX_OPS = 20
HH_MM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

OP_TYPES = ("ADD_ITEM", "REMOVE_ITEM", "UPDATE_ITEM", "MOVE_ITEM", "REORDER_ITEM", "UPDATE_DAY")


class AdjustError(Exception):
    """建议无法产出。message 会直接展示给用户，必须是人话。"""

    def __init__(self, message: str, code: int = 10003):
        super().__init__(message)
        self.message = message
        self.code = code


def _text(node: Any) -> str | None:
    if node is None:
        return None
    value = str(node).strip()
    return value or None


def _index_days(plan_view: dict) -> dict[int, set[str]]:
    """dayIndex → 该天 itemId 集合。用于「重排必须给全」与「item 是否属于这一天」的判断。"""
    result: dict[int, set[str]] = {}
    for day in plan_view.get("days") or []:
        index = day.get("dayIndex")
        if isinstance(index, int):
            result[index] = {str(i.get("itemId")) for i in (day.get("items") or []) if i.get("itemId")}
    return result


def validate_ops(ops: Any, plan_view: dict) -> list[str]:
    """返回人类可读的问题列表；空列表代表通过。"""
    if not isinstance(ops, list):
        return ["调整操作必须是数组"]
    if len(ops) > MAX_OPS:
        return [f"一次调整最多 {MAX_OPS} 项，收到 {len(ops)} 项"]

    day_items = _index_days(plan_view)
    all_item_ids = {i for ids in day_items.values() for i in ids}
    problems: list[str] = []

    for position, op in enumerate(ops, start=1):
        if not isinstance(op, dict):
            problems.append(f"第 {position} 项不是对象")
            continue
        kind = str(op.get("op") or "").upper()
        if kind not in OP_TYPES:
            problems.append(f"第 {position} 项的操作类型「{op.get('op')}」不支持")
            continue

        if kind == "ADD_ITEM":
            if op.get("dayIndex") not in day_items:
                problems.append(f"第 {position} 项要加到不存在的第 {op.get('dayIndex')} 天")
            if not _text(op.get("title")):
                problems.append(f"第 {position} 项新增行程缺少标题")
            for field in ("startTime", "endTime"):
                value = _text(op.get(field))
                if value and not HH_MM.match(value):
                    problems.append(f"第 {position} 项的 {field} 不是 HH:mm 格式")
            continue

        if kind == "REORDER_ITEM":
            index = op.get("dayIndex")
            if index not in day_items:
                problems.append(f"第 {position} 项重排了不存在的第 {index} 天")
                continue
            ordered = op.get("orderedItemIds") or []
            if not isinstance(ordered, list) or not ordered:
                problems.append(f"第 {position} 项重排没有给出 orderedItemIds")
                continue
            given = [str(x) for x in ordered]
            if len(set(given)) != len(given):
                problems.append(f"第 {position} 项重排列表里有重复项")
            # 必须给全：只给一部分时「没提到的那些排哪」没有答案，最后会变成顺序莫名变化
            if set(given) != day_items[index]:
                problems.append(f"第 {position} 项重排必须列出第 {index} 天的全部行程项")
            continue

        if kind == "UPDATE_DAY":
            index = op.get("dayIndex")
            if index not in day_items:
                problems.append(f"第 {position} 项修改了不存在的第 {index} 天")
                continue
            changes = op.get("changes")
            if not isinstance(changes, list) or not changes:
                problems.append(f"第 {position} 项没有给出 changes")
                continue
            for change in changes:
                field = change.get("field") if isinstance(change, dict) else None
                if field not in DAY_FIELDS:
                    problems.append(f"第 {position} 项不支持修改天的字段「{field}」")
            continue

        item_id = str(op.get("itemId") or "")
        if not item_id:
            problems.append(f"第 {position} 项缺少 itemId")
            continue
        if item_id not in all_item_ids:
            problems.append(f"第 {position} 项引用了计划里不存在的行程项（itemId={item_id}）")
            continue

        if kind == "MOVE_ITEM":
            if op.get("toDayIndex") not in day_items:
                problems.append(f"第 {position} 项要移到不存在的第 {op.get('toDayIndex')} 天")
            continue

        if kind == "UPDATE_ITEM":
            changes = op.get("changes")
            if not isinstance(changes, list) or not changes:
                problems.append(f"第 {position} 项没有给出 changes")
                continue
            for change in changes:
                if not isinstance(change, dict):
                    problems.append(f"第 {position} 项的 changes 里有非对象元素")
                    continue
                field = change.get("field")
                if field not in ITEM_FIELDS:
                    problems.append(f"第 {position} 项不支持修改字段「{field}」")
                    continue
                value = _text(change.get("after"))
                if field in ("startTime", "endTime") and value and not HH_MM.match(value):
                    problems.append(f"第 {position} 项的 {field} 不是 HH:mm 格式")
                if field == "title" and not value:
                    problems.append(f"第 {position} 项把标题改成了空")

    return problems


def mock_adjust(plan_view: dict) -> dict:
    """确定性产出，供 mock 模式与契约回归使用。

    选择第一个**还没有备注**的行程项改备注。这一策略是为了让同一份计划连续被调整时
    每次都有实际变化：如果无脑改第一项，第二次调用会和第一次给出同样的值，
    被服务端的「无变更」判定拒掉（30110），本地联调会莫名其妙地卡住。
    所有项都写过备注之后改为新增一项——mock 不需要语义正确，只需要每次都能产生变更。
    """
    days = plan_view.get("days") or []
    for day in days:
        for item in day.get("items") or []:
            if not (item.get("note") or "").strip():
                return {
                    "summary": f"调整「{item.get('title')}」的备注",
                    "ops": [{
                        "op": "UPDATE_ITEM",
                        "itemId": str(item.get("itemId")),
                        "changes": [{"field": "note", "after": "按当前的安排来"}],
                    }],
                }
    if days:
        return {
            "summary": "新增一项自由安排",
            "ops": [{
                "op": "ADD_ITEM",
                "dayIndex": days[0].get("dayIndex"),
                "title": "自由安排",
                "startTime": None,
                "endTime": None,
                "note": None,
            }],
        }
    return {"summary": "这份行程还没有可调整的行程项", "ops": []}


async def run_adjust(provider: LLMProvider, plan_view: dict, instruction: str,
                     trace_id: str = "") -> dict:
    """产出建议。失败抛 {@link AdjustError}。"""
    if not instruction or not instruction.strip():
        raise AdjustError("请先说要调整什么，比如「下午不去了」")

    real = bool(provider.describe().get("real"))
    if not real:
        result = mock_adjust(plan_view)
        logger.info("adjust(mock) trace=%s ops=%d", trace_id, len(result["ops"]))
        return {**result, "mock": True}

    messages = [ChatMessage(role=m["role"], content=m["content"])
                for m in build_adjust_messages(plan_view, instruction)]
    chunks: list[str] = []
    try:
        async for piece in provider.stream_chat(messages, trace_id=trace_id, json_mode=True):
            chunks.append(piece)
    except LLMError as e:
        logger.error("adjust llm failed trace=%s err=%s", trace_id, e)
        raise AdjustError(f"调整建议生成失败：{e}", code=50201) from e

    raw = "".join(chunks)
    parsed, error, _reason = extract_json(raw)
    if parsed is None:
        logger.warning("adjust parse failed trace=%s err=%s head=%s", trace_id, error, raw[:200])
        raise AdjustError("模型返回的内容不是合法 JSON，请重试")

    summary = _text(parsed.get("summary")) or "行程调整建议"
    ops = parsed.get("ops")
    problems = validate_ops(ops, plan_view)
    if problems:
        logger.warning("adjust rejected trace=%s count=%d first=%s", trace_id, len(problems), problems[0])
        raise AdjustError("建议内容不合法：" + problems[0])

    logger.info("adjust(real) trace=%s ops=%d", trace_id, len(ops or []))
    return {"summary": summary[:100], "ops": ops or [], "mock": False}
