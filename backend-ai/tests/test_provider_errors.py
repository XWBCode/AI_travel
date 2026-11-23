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
