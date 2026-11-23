"""和风天气 JWT（EdDSA / Ed25519）生成与缓存。

## 为什么是 JWT 而不是 API KEY（D-03 决定）

不是因为"JWT 更安全"这种空话，而是 API KEY 有一条**明确的、有日期的、不由我们控制的失效路径**：
官方 `authentication` 页写「从 2027年1月1日起限制 API KEY 的每日请求数量」，
`project-and-key` 页写「从 2027年2月1日起逐步限制」（两处日期不一致，方向一致），
且 SDK 5+ 已不支持 API KEY。选 JWT 等于把未来一次被迫迁移提前做掉。

## 参数口径（逐条来自 dev.qweather.com/docs/configuration/authentication/）

- header：`alg = EdDSA`，`kid = 凭据ID`
- payload：`iss = 开发者ID`（Q 开头 10 位）、`sub = 项目ID`、`iat = now - 30`、`exp = iat + 900`
- 有效期**最长 24 小时**；官方建议 `iat` 回退 30 秒防时钟误差
- 保留字段 `aud` / `nbf` 不要带；`typ` 若带必须是 `JWT`（PyJWT 默认带，合规）

## 私钥

私钥**不入仓库、不进日志、不出现在响应里**（`.gitignore` 已含 `*.pem` / `*.key`）。
本模块只接受"路径"或"内容"两种来源，且异常信息里绝不回显私钥内容。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger("ai-travel.weather.jwt")

# 官方建议：iat 回退 30 秒，防止服务器时钟快于上游
CLOCK_SKEW_SECONDS = 30
# 单次 token 寿命。官方上限 24h；取 15 分钟——刷新成本只是一次本地签名，不值得为省它延长窗口
TOKEN_TTL_SECONDS = 900
# 提前这么多秒换新 token，避免"刚好在请求途中过期"
REFRESH_MARGIN_SECONDS = 120


class JwtConfigError(Exception):
    """凭据没配齐 / 私钥读不出来。属于配置问题，不是上游问题。"""


@dataclass
class QWeatherTokenProvider:
    """线程内缓存 JWT；到期前自动换新。

    `clock` 可注入，测试里用假时钟验证"提前刷新"与"401 后强制失效"两条路径，
    不需要真的睡 15 分钟。
    """

    developer_id: str
    project_id: str
    key_id: str
    private_key_pem: str = field(repr=False)  # repr=False：避免私钥被打进日志/异常
    ttl_seconds: int = TOKEN_TTL_SECONDS
    clock: Callable[[], float] = time.time

    _token: str | None = field(default=None, init=False, repr=False)
    _expires_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("developer_id", self.developer_id),
                ("project_id", self.project_id),
                ("key_id", self.key_id),
                ("private_key_pem", self.private_key_pem),
            )
            if not (value or "").strip()
        ]
        if missing:
            raise JwtConfigError("和风天气凭据缺少：" + "、".join(missing))
        if not self.private_key_pem.lstrip().startswith("-----BEGIN"):
            raise JwtConfigError("私钥不是 PEM 格式（应以 -----BEGIN 开头）")
        if self.ttl_seconds > 86400:
            # 官方上限 24 小时，超过会被拒
            raise JwtConfigError("JWT 有效期不能超过 24 小时")

    @classmethod
    def from_settings(cls, settings: object, *, clock: Callable[[], float] = time.time) -> "QWeatherTokenProvider":
        """从配置构造。私钥优先取文件路径，其次取内联内容（CI 用）。"""
        path = str(getattr(settings, "qweather_private_key_path", "") or "").strip()
        pem = str(getattr(settings, "qweather_private_key_pem", "") or "").strip()
        if not pem and path:
            try:
                pem = Path(path).read_text(encoding="utf-8")
            except OSError as e:
                # 只报路径与错误类型，不回显内容
                raise JwtConfigError(f"私钥文件读取失败：{path}（{type(e).__name__}）") from None
        return cls(
            developer_id=str(getattr(settings, "qweather_developer_id", "") or ""),
            project_id=str(getattr(settings, "qweather_project_id", "") or ""),
            key_id=str(getattr(settings, "qweather_key_id", "") or ""),
            private_key_pem=pem,
            clock=clock,
        )

    def token(self) -> str:
        """取当前可用的 JWT。未过期就直接复用，避免每次请求都签一遍。"""
        now = self.clock()
        if self._token and now < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token
        return self._issue(now)

    def invalidate(self) -> None:
        """上游回 401 时调用：丢掉缓存，下次强制重签。"""
        self._token = None
        self._expires_at = 0.0

    def _issue(self, now: float) -> str:
        import jwt  # 本地依赖：requirements.txt 已声明 PyJWT + cryptography

        iat = int(now) - CLOCK_SKEW_SECONDS
        exp = iat + self.ttl_seconds
        payload = {
            "iss": self.developer_id,
            "sub": self.project_id,
            "iat": iat,
            "exp": exp,
        }
        headers = {"kid": self.key_id}
        try:
            token = jwt.encode(payload, self.private_key_pem, algorithm="EdDSA", headers=headers)
        except Exception as e:  # noqa: BLE001 —— 只有这一处需要把库异常收敛成配置异常
            # 不把异常原文透出：PyJWT 的报错有时会带密钥片段
            raise JwtConfigError(f"JWT 签名失败（{type(e).__name__}）") from None
        if isinstance(token, bytes):  # PyJWT 1.x 返回 bytes
            token = token.decode("ascii")
        self._token = token
        self._expires_at = float(exp)
        logger.debug("已签发和风天气 JWT exp=%s kid=%s", exp, self.key_id)
        return token
