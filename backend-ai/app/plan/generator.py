"""计划生成编排：意图 → 生成 → 校验 → 事件。

与 05 文档 §3 的降级流程严格对应：
  生成 → 校验通过 → plan_draft
       → 校验失败 → plan_rejected + error（业务表写入 0 条，不自动修复、不自动重试）

"不自动修复"是刻意的：自动补全等于替模型编造，会让 AC-07 的防线形同虚设。
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, AsyncGenerator

from app.llm.prompts import build_plan_messages
from app.llm.provider import ChatMessage, LLMError, LLMProvider
from app.plan import mock
from app.plan.contract import count_fact_fields, verify
from app.plan.intent import TravelIntent

logger = logging.getLogger("ai-travel.plan")

# 拒绝原因（与 05 文档 §3 的取值对齐）
REASON_NEED_MORE_INFO = "NEED_MORE_INFO"
REASON_SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
REASON_INVALID_FORMAT = "INVALID_FORMAT"
REASON_TRUNCATED = "TRUNCATED"

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def extract_json(text: str, finish_reason: str | None = None) -> tuple[dict[str, Any] | None, str, str]:
    """从模型输出里提取 JSON 对象。

    返回 (plan, error, reason)。真实模型经常把 JSON 包在代码块里，
    或加一句"以下是您的行程"，所以先剥围栏、再退化到首尾花括号截取。

    finish_reason 是上游给的结束原因。它比"数花括号"更可靠：
    `length` 说明是我们把 max_tokens 给少了（可调），其余才是模型自己写崩（不可调）。
    区分这两者，用户才知道该"重试"还是该"把行程说短一点"。
    """
    truncated_by_limit = finish_reason == "length"

    if not text or not text.strip():
        reason = REASON_TRUNCATED if truncated_by_limit else REASON_INVALID_FORMAT
        return None, "模型返回空内容", reason

    cleaned = _FENCE.sub("", text.strip())
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed, "", ""
        return None, "顶层不是 JSON 对象", REASON_INVALID_FORMAT
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0:
        return None, "输出中未找到 JSON 对象", REASON_INVALID_FORMAT
    if end <= start:
        detail = "JSON 未闭合（可能被截断）"
        if finish_reason:
            detail += f"；输出 {len(text)} 字符，finish_reason={finish_reason}"
        return None, detail, REASON_TRUNCATED

    fragment = cleaned[start:end + 1]
    try:
        parsed = json.loads(fragment)
    except json.JSONDecodeError as e:
        # 末尾缺花括号最常见的成因就是输出被 max_tokens 截断
        reason = REASON_TRUNCATED if truncated_by_limit or not fragment.rstrip().endswith("}") \
            else REASON_INVALID_FORMAT
        detail = f"JSON 解析失败: {e.msg}（位置 {e.pos}）"
        if reason == REASON_TRUNCATED:
            detail += f"；输出 {len(text)} 字符，finish_reason={finish_reason or '未知'}"
        return None, detail, reason

    if not isinstance(parsed, dict):
        return None, "顶层不是 JSON 对象", REASON_INVALID_FORMAT
    return parsed, "", ""


async def _collect(provider: LLMProvider, history: list[dict[str, str]],
                   trace_id: str) -> tuple[str, str | None]:
    """把流式输出拼成完整文本。计划场景必须拿到完整 JSON 才能校验。

    同时带回 finish_reason：截断与"模型写崩"是两种病，只有上游能告诉我们哪一种。
    """
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in build_plan_messages(history)]
    chunks: list[str] = []
    async for piece in provider.stream_chat(messages, trace_id=trace_id, json_mode=True):
        chunks.append(piece)
    return "".join(chunks), getattr(provider, "last_finish_reason", None)


async def run_generation(
    provider: LLMProvider,
    intent: TravelIntent,
    history: list[dict[str, str]],
    trace_id: str = "",
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """产出 (事件名, data) 序列。路由层负责转成 SSE 文本。"""
    start_at = time.perf_counter()
    model = provider.describe().get("model", "unknown")
    real = bool(provider.describe().get("real"))

    yield "intent", intent.to_payload()

    # 信息不足以生成时不下发 plan_draft，让前端按 missingFields 追问
    if not intent.need_plan:
        logger.info("plan skipped trace=%s reason=%s missing=%s",
                    trace_id, REASON_NEED_MORE_INFO, intent.missing_fields)
        yield "done", {
            "model": model,
            "provider": provider.describe().get("provider"),
            "latencyMs": int((time.perf_counter() - start_at) * 1000),
            "skipped": REASON_NEED_MORE_INFO,
            "missingFields": intent.missing_fields,
        }
        return

    draft_id = uuid.uuid4().hex

    if not real:
        # Mock 分支：确定性生成，必过校验。它同时是"契约回归"的基准产出
        plan = mock.build_plan(intent)
        problems = verify(plan)
        if problems:
            # Mock 自己都过不了校验，说明契约或生成器坏了，必须立刻暴露而不是静默下发
            logger.error("mock plan 未通过自检 trace=%s problems=%s", trace_id, problems[:5])
            yield "plan_rejected", {"reason": REASON_SCHEMA_VIOLATION, "errors": problems[:20], "traceId": trace_id}
            yield "error", {"code": 10002, "message": "计划生成失败，请重试"}
            return
        yield "plan_draft", {"draftId": draft_id, "plan": plan, "mock": True}
        yield "done", {
            "model": model,
            "provider": provider.describe().get("provider"),
            "latencyMs": int((time.perf_counter() - start_at) * 1000),
            "draftId": draft_id,
        }
        logger.info("plan draft(mock) trace=%s days=%s factFields=%s",
                    trace_id, intent.slots.get("days"), plan.get("unverifiedFieldCount"))
        return

    try:
        raw, finish_reason = await _collect(provider, history, trace_id)
    except LLMError as e:
        logger.error("plan llm failed trace=%s err=%s", trace_id, e)
        yield "error", {"code": 50201, "message": str(e)}
        return

    plan, parse_error, reason = extract_json(raw, finish_reason)
    if plan is None:
        logger.warning("plan parse failed trace=%s reason=%s finish=%s chars=%d err=%s raw_head=%s",
                       trace_id, reason, finish_reason, len(raw), parse_error, raw[:200])
        rejected = {"reason": reason, "errors": [parse_error], "traceId": trace_id}
        if finish_reason:
            rejected["finishReason"] = finish_reason
        yield "plan_rejected", rejected
        # 截断是「我们的上限不够」，不是「模型不会写」：提示要能指导用户下一步，
        # 但绝不在服务端偷偷重试——重试一次就是再烧一次额度，且掩盖真实失败率。
        message = "行程太长，输出被截断，请把天数说少一点或分批规划" if reason == REASON_TRUNCATED \
            else "生成结果格式不正确，请重试"
        yield "error", {"code": 10002, "message": message}
        return

    # unverifiedFieldCount 是派生统计量：数的是"整份 JSON 里有几个待核实事实字段"。
    # 让模型自己数并不可靠（实测每个模型都会数错，且错多少全看运气），
    # 这里按 count_fact_fields 重算覆盖 —— 这不是"替模型补内容"，
    # 只是把一个机械统计量的计算权从模型手里收回来。
    declared = plan.get("unverifiedFieldCount")
    recounted = count_fact_fields(plan)
    if declared != recounted:
        logger.info("plan unverifiedFieldCount 重算 trace=%s declared=%s actual=%s",
                    trace_id, declared, recounted)
        plan["unverifiedFieldCount"] = recounted

    problems = verify(plan)
    if problems:
        # 原始响应由 Java 侧写 t_ai_generation_log 留痕（本服务不落库）
        logger.warning("plan rejected trace=%s count=%d first=%s", trace_id, len(problems), problems[0])
        yield "plan_rejected", {
            "reason": REASON_SCHEMA_VIOLATION,
            "errors": problems[:20],
            "traceId": trace_id,
        }
        yield "error", {"code": 10002, "message": "生成结果未通过校验，请重试"}
        return

    yield "plan_draft", {"draftId": draft_id, "plan": plan, "mock": False}
    yield "done", {
        "model": model,
        "provider": provider.describe().get("provider"),
        "latencyMs": int((time.perf_counter() - start_at) * 1000),
        "draftId": draft_id,
    }
    logger.info("plan draft(real) trace=%s days=%s factFields=%s",
                trace_id, intent.slots.get("days"), plan.get("unverifiedFieldCount"))
