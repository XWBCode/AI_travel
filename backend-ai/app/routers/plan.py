"""计划生成接口。

两个端点：
- `POST /v1/plan/intent`  只判意图（普通 JSON），供 Java 在对话前预判、或前端调试用
- `POST /v1/plan/stream`  SSE 生成计划，事件见 05 文档 §4

与 Java 的分工：本服务只生成 + 自校验，不落库、不判权限。
`plan_draft` 下发的是**已通过契约校验**的内容，不是模型原始输出。
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.llm.provider import LLMError
from app.plan.generator import run_generation
from app.plan.intent import analyze_messages

logger = logging.getLogger("ai-travel.plan")

router = APIRouter(prefix="/v1", tags=["plan"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证事件逐条到达
    "Connection": "keep-alive",
}


class MessageIn(BaseModel):
    role: str = Field(..., description="user / assistant")
    content: str = Field(..., description="消息内容")


class PlanRequest(BaseModel):
    messages: list[MessageIn]
    sessionId: str | None = Field(default=None, description="Java 侧会话 ID，仅用于串联日志")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _history(req: PlanRequest) -> list[dict[str, str]]:
    history = [{"role": m.role, "content": m.content} for m in req.messages]
    if not any(m["role"] == "user" for m in history):
        raise LLMError("消息列表中缺少用户消息")
    return history


@router.post("/plan/intent")
async def plan_intent(req: PlanRequest, request: Request):
    """规则判定意图与槽位。不调模型，毫秒级返回。"""
    trace_id = request.headers.get("X-Trace-Id", "")
    intent = analyze_messages(_history(req))
    logger.info("intent trace=%s scene=%s needPlan=%s missing=%s",
                trace_id, intent.scene, intent.need_plan, intent.missing_fields)
    return {
        "code": 0,
        "data": {
            **intent.to_payload(),
            # 槽位比 intent 事件多给一层：Java 侧可直接拿去建草稿，不用重复解析文本
            "slots": intent.slots,
            "followUps": intent.follow_ups,
        },
        "traceId": trace_id,
    }


@router.post("/plan/generate")
async def plan_generate(req: PlanRequest, request: Request):
    """非流式生成：返回完整 JSON，供 Java 落库使用。

    与 `/plan/stream` 共用同一条 run_generation 流水线（同样的校验、同样的 mock 分支），
    只是把事件收集成一个响应体。分成两个端点而不是让 Java 去解析自己的 SSE：
    跨语言解析 SSE 很容易在心跳、分包、事件名上踩坑（M0 就踩过），没必要重复一次。
    """
    provider = request.app.state.provider
    trace_id = request.headers.get("X-Trace-Id", "")
    history = _history(req)

    intent_payload: dict | None = None
    draft: dict | None = None
    rejected: dict | None = None
    error: dict | None = None
    done: dict = {}

    async for event, data in run_generation(provider, analyze_messages(history), history, trace_id):
        if event == "intent":
            intent_payload = data
        elif event == "plan_draft":
            draft = data
        elif event == "plan_rejected":
            rejected = data
        elif event == "error":
            error = data
        elif event == "done":
            done = data

    data = {
        "intent": intent_payload,
        "draft": draft,
        "rejected": rejected,
        "skipped": done.get("skipped"),
        "missingFields": done.get("missingFields"),
        "done": done,
    }
    if error is not None:
        return {
            "code": error.get("code", 50000),
            "message": error.get("message", "计划生成失败"),
            "data": data,
            "traceId": trace_id,
        }
    return {"code": 0, "message": "ok", "data": data, "traceId": trace_id}


@router.post("/plan/stream")
async def plan_stream(req: PlanRequest, request: Request):
    provider = request.app.state.provider
    trace_id = request.headers.get("X-Trace-Id", "")
    history = _history(req)
    intent = analyze_messages(history)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for event, data in run_generation(provider, intent, history, trace_id):
                # 05 文档要求所有事件都带 traceId，统一在这里注入，避免每个分支各写一遍
                yield _sse(event, {**data, "traceId": trace_id})
        except Exception as e:  # 兜底：流绝不能无声中断
            logger.exception("plan stream unexpected error trace=%s", trace_id)
            yield _sse("error", {"code": 50000, "message": f"计划服务内部错误: {type(e).__name__}", "traceId": trace_id})

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)
