"""模型网关的错误识别与密钥脱敏。

真实踩到的坑：网关在 API Key 失效时返回的是 **HTTP 200 + application/json 错误体**
（{"code":400,"message":"invalid apikey: sk-..."}），而调用侧按 SSE 逐行解析，
读到零个事件，最后报「模型返回空内容」。排查时会一路怀疑 prompt 和模型。

这里把两条兜底钉住：
1. 错误体能被翻译成人话，而不是当作「空内容」；
2. 错误体里回显的密钥必须被抹掉——上游确实会把 key 原样写在 message 里。
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "mock")

from app.llm.provider import _describe_gateway_error, mask_secret  # noqa: E402

LEAKY = '{"code":400,"message":"invalid apikey: sk-8TBMZE5bSWsB9YCXzOaWTRYgBJnxdz6a","data":null}'


def test_masks_api_key():
    masked = mask_secret("invalid apikey: sk-8TBMZE5bSWsB9YCXzOaWTRYgBJnxdz6a")
    assert "sk-8TBM" not in masked
    assert "sk-***" in masked


def test_describes_code_and_message():
    described = _describe_gateway_error(LEAKY)
    assert "400" in described
    assert "invalid apikey" in described
    # 关键：翻译之后仍然不能带出原始密钥
    assert "Z6a" not in described.split("sk-***")[-1]


def test_falls_back_to_raw_text_for_non_json():
    described = _describe_gateway_error("<html>502 Bad Gateway</html>")
    assert "502" in described


def test_plain_string_with_key_is_also_masked():
    assert "sk-8TBM" not in _describe_gateway_error("auth failed: sk-8TBMZE5bSWsB9YCXzOaWTRYgBJnxdz6a")


def test_captures_finish_reason_from_sse(monkeypatch):
    """finish_reason 必须从流里捞出来。

    没有它，截断和"模型写崩"只能靠数花括号猜；有了它，才知道该调 max_tokens
    还是该让用户重试。这是 BUG-009 的判别依据。
    """
    import asyncio

    import httpx

    from app.llm.provider import ChatMessage, OpenAICompatibleProvider

    body = (
        'data: {"choices":[{"delta":{"content":"{\\"a\\": 1"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler), timeout=5)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    provider = OpenAICompatibleProvider("http://stub/v1", "stub-model", "sk-stub", 30, 100, 0.2)

    async def run() -> str:
        return "".join([p async for p in provider.stream_chat([ChatMessage("user", "hi")], json_mode=True)])

    text = asyncio.run(run())
    assert text == '{"a": 1'
    assert provider.last_finish_reason == "length"
