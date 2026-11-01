"""LLM Provider 抽象层。

设计要求（总纲 4.6）：
1. LLM 调用封装成统一接口，替换服务商时不改上层代码。
2. API Key 只在服务端使用，不落日志、不返回前端。
3. Mock 模式必须显式标注，不能伪装成真实模型输出（总纲 9.3）。

接入新服务商的方式：实现 LLMProvider 即可，目前 OpenAI 兼容协议已覆盖
DeepSeek / 通义千问(兼容模式) / 智谱 / Kimi / 硅基流动 / OpenAI 等。
"""

from __future__ import annotations

import abc
import asyncio
import json
from dataclasses import dataclass
from typing import AsyncGenerator

import httpx


class LLMError(RuntimeError):
    """LLM 调用异常，上层统一转成 SSE error 事件。"""


@dataclass
class ChatMessage:
    role: str
    content: str


class LLMProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def stream_chat(self, messages: list[ChatMessage], trace_id: str = "") -> AsyncGenerator[str, None]:
        """流式返回增量文本。"""
        raise NotImplementedError

    @abc.abstractmethod
    def describe(self) -> dict:
        """返回可对外暴露的非敏感信息，用于 /v1/health。"""
        raise NotImplementedError


class MockProvider(LLMProvider):
    """本地开发用假模型。

    明确标记 [MOCK]，避免被误认为真实 AI 能力。
    它会真实地按 token 分片推送，用于验证 SSE 全链路是否通畅。
    """

    name = "mock"

    def __init__(self) -> None:
        self._model_label = "mock-model"

    async def stream_chat(self, messages: list[ChatMessage], trace_id: str = "") -> AsyncGenerator[str, None]:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        reply = (
            "[MOCK] 当前为本地 Mock 模式，未调用真实大模型。\n\n"
            f"我收到的最后一条消息是：{last_user[:200]}\n\n"
            "配置方式：在 .env 中设置 LLM_PROVIDER=openai_compatible、LLM_BASE_URL、"
            "LLM_MODEL、LLM_API_KEY 后重启 Python 服务。\n\n"
            "这条回复用于验证以下链路是否全部打通：\n"
            "1. Vue3 → Java 的 SSE 转发\n"
            "2. Java → Python 的流式请求\n"
            "3. 消息写入 MySQL 并在刷新后恢复\n"
        )
        # 按字符分片，模拟真实流式节奏
        for i in range(0, len(reply), 6):
            await asyncio.sleep(0.02)
            yield reply[i:i + 6]

    def describe(self) -> dict:
        return {"provider": self.name, "model": self._model_label, "real": False}


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容协议的流式实现。"""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str, timeout_seconds: int,
                 max_tokens: int, temperature: float) -> None:
        if not api_key:
            raise LLMError("LLM_PROVIDER=openai_compatible 时必须配置 LLM_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def stream_chat(self, messages: list[ChatMessage], trace_id: str = "") -> AsyncGenerator[str, None]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        timeout = httpx.Timeout(self._timeout, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self._base_url}/chat/completions",
                                         json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="ignore")[:500]
                        # 注意：日志和异常中绝不输出请求头，避免密钥泄漏
                        raise LLMError(f"模型服务返回 {resp.status_code}: {body}")
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield piece
        except httpx.TimeoutException as e:
            raise LLMError("模型服务响应超时") from e
        except httpx.HTTPError as e:
            raise LLMError(f"模型服务连接失败: {type(e).__name__}") from e

    def describe(self) -> dict:
        return {"provider": self.name, "model": self._model, "real": True}


def build_provider(cfg) -> LLMProvider:
    if cfg.is_mock:
        return MockProvider()
    if cfg.provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            timeout_seconds=cfg.timeout_seconds,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
        )
    raise LLMError(f"不支持的 LLM_PROVIDER: {cfg.provider}")
