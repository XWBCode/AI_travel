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
import re
from dataclasses import dataclass
from typing import AsyncGenerator

import httpx


class LLMError(RuntimeError):
    """LLM 调用异常，上层统一转成 SSE error 事件。"""


# 上游网关的错误体常把请求里的密钥原样回显（「invalid apikey: sk-xxx」）。
# 这类文本一旦进了日志或返回给前端，就等于把密钥公开了，所有对外消息都要过一遍这里。
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def mask_secret(text: str) -> str:
    return _SECRET_PATTERN.sub("sk-***", text)


def _describe_gateway_error(body: str) -> str:
    """把网关错误体翻成一句能直接看懂的日志。

    上游有两种失败形态：非 200 带错误体，以及 **200 但 body 是错误 JSON**（Key 失效就是这样）。
    后者若只按 SSE 逐行解析，读到的永远是零个事件，最终报「模型返回空内容」——
    排查时会一路怀疑 prompt 和模型，真正的原因却是凭证过期。
    """
    text = body.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            message = data.get("message") or data.get("error") or text
            code = data.get("code")
            prefix = f"网关错误码 {code}：" if code else "网关返回错误："
            return mask_secret(f"{prefix}{message}")
    except json.JSONDecodeError:
        pass
    return mask_secret(text[:300])


@dataclass
class ChatMessage:
    role: str
    content: str


class LLMProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def stream_chat(self, messages: list[ChatMessage], trace_id: str = "",
                          json_mode: bool = False) -> AsyncGenerator[str, None]:
        """流式返回增量文本。

        json_mode=True 时请求服务商按严格 JSON 返回（OpenAI 兼容的 response_format）。
        计划生成场景用它把"模型自由发挥"的概率压低一个数量级；Mock 实现忽略该参数。
        """
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

    async def stream_chat(self, messages: list[ChatMessage], trace_id: str = "",
                          json_mode: bool = False) -> AsyncGenerator[str, None]:
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
                 max_tokens: int, temperature: float, json_mode: bool = True) -> None:
        if not api_key:
            raise LLMError("LLM_PROVIDER=openai_compatible 时必须配置 LLM_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature
        # 部分自建/代理服务不认 response_format，会直接 400；可用 LLM_JSON_MODE=false 关掉
        self._json_mode_supported = json_mode
        # 上一次流式调用结束时的 finish_reason（stop / length / content_filter ...）。
        # 单独留一个属性是因为 async generator 无法把"结束原因"作为返回值带出来，
        # 而 length 正是 BUG-009 的判别依据：内容是模型编坏了，还是我们给的上限太小。
        self.last_finish_reason: str | None = None

    async def stream_chat(self, messages: list[ChatMessage], trace_id: str = "",
                          json_mode: bool = False) -> AsyncGenerator[str, None]:
        self.last_finish_reason = None
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            # 生成结构化 JSON 时压低随机性，明显减少字段漂移
            "temperature": 0.2 if json_mode else self._temperature,
            "max_tokens": self._max_tokens,
        }
        if json_mode and self._json_mode_supported:
            payload["response_format"] = {"type": "json_object"}
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
                        # 注意：日志和异常中绝不输出请求头；错误体也要脱敏（上游会回显密钥）
                        raise LLMError(mask_secret(f"模型服务返回 {resp.status_code}: {body}"))
                    # 200 + application/json = 网关把错误包进了正常响应（典型是 invalid apikey）。
                    # 不识别这一层，下面按 SSE 逐行读只会读到零个事件，最后误报「模型返回空内容」。
                    content_type = (resp.headers.get("content-type") or "").lower()
                    if "application/json" in content_type and "event-stream" not in content_type:
                        body = (await resp.aread()).decode("utf-8", errors="ignore")
                        raise LLMError(_describe_gateway_error(body))

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
                            # 流中途也可能夹一条 {"code":400,"message":...}，别当正常空块跳过
                            if chunk.get("code") not in (None, 0) or chunk.get("error"):
                                raise LLMError(_describe_gateway_error(data))
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield piece
                        # finish_reason 只在最后一块出现；有的网关放在 choices[0].finish_reason，
                        # 也有的放在顶层，两处都取，取到即记
                        reason = choices[0].get("finish_reason") or chunk.get("finish_reason")
                        if reason:
                            self.last_finish_reason = str(reason)
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
            json_mode=cfg.json_mode,
        )
    raise LLMError(f"不支持的 LLM_PROVIDER: {cfg.provider}")
