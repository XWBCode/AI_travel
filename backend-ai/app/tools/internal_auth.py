"""内部端点鉴权（TD-01 修复）。

**设计取舍：fail-closed。**
没配置 `AI_INTERNAL_SECRET` 时，内部端点一律 403，而不是"没密钥就放行"。

理由不是"更安全"这么空泛，而是三条具体事实：
1. 内部端点的读者是运维（灰度观察、排障），不是业务方。运维能配密钥，业务方不需要读它。
2. 反之，"没密钥就放行"会让一个只读端点成为"整个 AI 服务对公网无鉴权"的默认事实——
   而反例只需要一次误配：`.env` 忘了写、容器环境变量没挂、换了台机器。
3. 这台机器上真实发生过：`backend-ai/.env` 没有这个键，运行中的服务是 fail-open。
   代码默认放行 = 缺陷一定会复发；代码默认拒绝 = 缺陷只会表现为"读不到"，一眼可见。

代价：读指标前必须先配密钥。这是可接受的，因为配密钥本来就是要开灰度/上生产的前置条件。
"""

from __future__ import annotations

from typing import Mapping, Optional

HEADER_TOKEN = "x-internal-token"

# 400 段沿用项目既有 HTTP 码：40300 是"令牌不对"，40301 是"服务端没配密钥"。
# 分成两个码是为了排障时能一眼分清"我令牌错了"还是"你服务没配"。
CODE_BAD_TOKEN = 40300
CODE_NOT_CONFIGURED = 40301


def check(headers: Mapping[str, str], secret: str) -> Optional[dict]:
    """内部端点鉴权。

    Args:
        headers: 请求头（大小写不敏感，Starlette 的 Headers 即可）。
        secret: 服务端配置的 `AI_INTERNAL_SECRET`。

    Returns:
        `None` 表示放行；否则返回可直接交给 `JSONResponse(content=...)` 的字典。
    """
    if not secret:
        # 没配密钥 → 拒绝。这是 TD-01 的核心：不把"未配置"当成"无需鉴权"。
        return {
            "code": CODE_NOT_CONFIGURED,
            "message": "internal token not configured on server",
        }

    token = headers.get(HEADER_TOKEN, "") or ""
    if not token or not _same(token, secret):
        return {"code": CODE_BAD_TOKEN, "message": "internal token required"}
    return None


def _same(left: str, right: str) -> bool:
    """常数时间比较。

    长度不同时 `compare_digest` 会直接返回 False 且不抛异常，
    这已经够用；单独包一层是为了让"为什么不用 `==`"这件事在代码里显式可见。
    """
    import hmac

    return hmac.compare_digest(left, right)
