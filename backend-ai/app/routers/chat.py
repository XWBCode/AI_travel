"""对话接口：SSE 流式输出。

事件协议与 Java 侧保持一致，见 ChatController 的注释。
本服务不落库、不做权限判断，只负责生成。
"""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.llm.prompts import build_messages
from app.llm.provider import ChatMessage, LLMError

logger = logging.getLogger("ai-travel.chat")

router = APIRouter(prefix="/v1", tags=["chat"])


class MessageIn(BaseModel):
    role: str = Field(..., description="user / assistant")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    messages: list[MessageIn]
    # 预留：后续按场景切换 system prompt 与 Agent 工作流
    scene: str = "general"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    provider = request.app.state.provider
    trace_id = request.headers.get("X-Trace-Id", "")

    raw = [{"role": m.role, "content": m.content} for m in req.messages]
    messages = build_messages(raw)

    if not any(m["role"] == "user" for m in messages):
        raise LLMError("消息列表中缺少用户消息")

    async def event_generator() -> AsyncGenerator[str, None]:
        start = time.perf_counter()
        produced = 0
        try:
            async for piece in provider.stream_chat(
                [ChatMessage(role=m["role"], content=m["content"]) for m in messages],
                trace_id=trace_id,
            ):
                produced += len(piece)
                yield _sse("delta", {"content": piece})
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.info("chat done trace=%s chars=%d latency=%dms", trace_id, produced, latency_ms)
            yield _sse("done", {
                "model": provider.describe().get("model", "unknown"),
                "provider": provider.describe().get("provider"),
                "latencyMs": latency_ms,
                "chars": produced,
            })
        except LLMError as e:
            logger.error("chat failed trace=%s err=%s", trace_id, e)
            yield _sse("error", {"code": 50201, "message": str(e)})
        except Exception as e:  # 兜底：绝不让流无声中断
            logger.exception("chat unexpected error trace=%s", trace_id)
            yield _sse("error", {"code": 50000, "message": f"AI 服务内部错误: {type(e).__name__}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证逐字下发
            "Connection": "keep-alive",
        },
    )
