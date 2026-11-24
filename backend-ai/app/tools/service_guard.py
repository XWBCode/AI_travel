"""AI 服务对 Java 的接口级鉴权（TD-02）。

**背景**：`/v1/plan/*`、`/v1/chat/*`、`/v1/weather` 原本没有任何鉴权。谁能连上 8000
就能直接调，绕过 Java 侧的登录态与"计划归属"校验，白烧模型额度。
当时的唯一防线是"8000 只绑回环"——那是网络边界，不是访问控制。

**四档判定**（按顺序，先命中先返回）：

| 条件 | 结果 | 理由 |
|---|---|---|
| 配了密钥 + 令牌正确 | 放行 | 正常路径（Java 带 `X-Internal-Token`） |
| 配了密钥 + 令牌缺失/错误 | 403 `40300` | 有密钥就只认密钥 |
| 未配密钥 + `APP_ENV=prod` | 403 `40301` | 生产不许出现"忘配密钥"这种状态 |
| 未配密钥 + 非 prod + 来源回环 | 放行 | dev 便利：本地直连调试不需要先造密钥 |
| 未配密钥 + 非 prod + 来源非回环 | 403 `40302` | 即使误把 host 设成 0.0.0.0，外部也进不来 |

**为什么不做成"没密钥就一律拒绝"**：那会让本机现有配置（`backend-ai/.env` 没有
`AI_INTERNAL_SECRET`）一次性全挂，而用户正在这台机器上开发。dev 便利是保留的，
但把它限制在**回环来源**上——"没密钥"不再等于"没边界"，这是与纯 fail-open 的关键区别。

**为什么不信任 `X-Forwarded-For`**：这条链路上没有反向代理（Java 直连），
任何客户端都能自己伪造这个头。只认 TCP 对端地址。
"""

from __future__ import annotations

import hmac
from typing import Mapping, Optional

HEADER_TOKEN = "x-internal-token"

CODE_BAD_TOKEN = 40300
CODE_NOT_CONFIGURED = 40301
CODE_NOT_LOOPBACK = 40302

_PROD_ENVS = ("prod", "production")
PROTECTED_PREFIX = "/v1/"

# 这些路径不走本闸门：
# - /v1/health：健康检查必须无条件可读，否则监控和 readiness 都得先配密钥
# - /v1/internal/*：由 internal_auth 自己负责（它的口径是"没密钥也拒绝"，比这里更严）
EXEMPT_PREFIXES = ("/v1/health", "/v1/internal/")


def is_protected(path: str) -> bool:
    """这条路径要不要过闸门。"""
    if not path.startswith(PROTECTED_PREFIX):
        return False
    return not any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def _loopback(host: Optional[str]) -> bool:
    """真实的来源判定。

    只认字面地址，不解析主机名——解析会引入 DNS 依赖与超时，而这判断发生在每个请求上。
    """
    if not host:
        return False
    return host == "::1" or host.startswith("127.")


def is_loopback(host: Optional[str]) -> bool:
    """`decide` 实际调用的入口。

    单独包一层是为了让**测试设施**能替换它，而正确性测试仍打在 `_loopback` 上：

    `TestClient` 的 TCP 对端是 `"testclient"`，既不是回环也不是真实地址。
    如果不替换，387 个存量用例会因为"来源不是回环"集体变红——那不是回归，
    是测试设施与真实部署形态不匹配。替换掉它之后，接口鉴权中"来源"这一档
    由 `test_service_guard.py` 直接对 `_loopback` 与 `decide` 做断言。
    """
    return _loopback(host)


def decide(
    headers: Mapping[str, str],
    *,
    secret: str,
    env: str,
    client_host: Optional[str],
) -> Optional[dict]:
    """返回 `None` 表示放行，否则返回可直接作为 JSONResponse 内容的字典。"""
    if secret:
        token = headers.get(HEADER_TOKEN, "") or ""
        if not token or not hmac.compare_digest(token, secret):
            return {"code": CODE_BAD_TOKEN, "message": "internal token required"}
        return None

    # 未配密钥
    if (env or "").strip().lower() in _PROD_ENVS:
        return {
            "code": CODE_NOT_CONFIGURED,
            "message": "internal token not configured on server",
        }
    if not is_loopback(client_host):
        return {
            "code": CODE_NOT_LOOPBACK,
            "message": "internal token not configured: non-loopback caller rejected",
        }
    return None
