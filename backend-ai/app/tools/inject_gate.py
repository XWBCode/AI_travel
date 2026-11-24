"""调整期天气注入的灰度闸门（M3-3 · S5）：决定"这一次请求到底要不要查天气"。

## 为什么需要这一层

S1~S4 的开关只有一颗：`inject_on_adjust`。它是**进程级**的——打开就是所有用户打开。
PM 批准的灰度方案是"内部账号白名单"，那就必须在进程级开关之下再判一次"**这个请求是谁**"。

## 为什么不能只信一个 `X-User-Id`

`X-User-Id` 是一个普通请求头。任何能连到 AI 服务 8000 端口的人都能写一个"1"，
于是"只有内部账号能用"就退化成"谁都能用"。PM 的原话是：只有可信 Java 服务能调用
Python 内部接口，Python 不接受外部伪造的用户身份。

所以身份用**共享密钥的 HMAC** 一起传：`X-User-Id: <id>` + `X-Identity-Sig: hmac(secret, "<id>|<traceId>")`。
拿不到密钥就伪造不出签名；密钥只在部署环境里，不进仓库、不进前端、不进日志。

## 失败一律不放行（fail-closed）

| 情形 | 结果 |
|---|---|
| `inject_on_adjust=false`（默认） | 不查、不注入（与 S4 之前逐字相同） |
| 数据源未启用（`provider=none`） | 不查 |
| kill 开关文件存在 | 不查（运行时可关，不用重启） |
| 配了白名单 + 身份缺失/签名不对 | 不查 |
| 配了白名单 + 用户不在名单里 | 不查 |
| **没配白名单** + `APP_ENV=prod` | 不查（`GRAY_NO_WHITELIST`）——生产上"忘了配"不能等于"对所有人开放" |
| 没配白名单 + dev/test/未声明 | 查（实例级开启，S4 的 63 项矩阵就是靠这条继续可跑） |

最后一行是刻意保留的逃生口：没配白名单时视为"整个实例都是内部实例"。
**已知边界**：生产忘设 `APP_ENV` 时这道闸门不生效（与 S1 的数据源合规闸门是同一条边界，
同一个兜底：发布检查清单）。要收紧成"未声明也拒绝"，得同时改 S1~S4 的 8 个已验收用例的环境，
那属于改动已验收行为，不在 S5 的范围内。

## 这里刻意不做的事

- 不解析 JWT、不查用户表：AI 服务不持会话，也不该成为第二个鉴权中心。
- 不按比例分流：PM 批的是"内部账号白名单"，按比例灰度需要会话侧支持，属 G3 之后的事。
- 不把白名单写进 `config/config.yaml`：那个文件随产物入库，灰度名单是运行期敏感配置。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import Settings

logger = logging.getLogger("ai-travel.weather")

# 请求头名。Java 侧 `AiServiceClient` 是唯一发送方，两边必须逐字一致。
HEADER_USER = "x-user-id"
HEADER_SIG = "x-identity-sig"

# 灰度放行
GRAY_ALLOWED = "GRAY_ALLOWED"

# 拒绝原因。**新造字面量而不是复用 M3-1 的 SKIP_***：那些枚举的意思是"天气查不到"，
# 而这里的绝大多数情形是"根本不该查"。混在一起，日志里就分不出"上游挂了"和"没放行"。
DENY_INJECT_DISABLED = "INJECT_DISABLED"      # 进程级开关未开（默认）
DENY_PROVIDER_DISABLED = "PROVIDER_DISABLED"  # 没有可用天气数据源
DENY_KILLED = "GRAY_KILLED"                   # 运行时急停
DENY_NO_WHITELIST = "GRAY_NO_WHITELIST"       # 生产环境没配白名单
DENY_UNTRUSTED = "IDENTITY_UNTRUSTED"         # 身份缺失/签名校验失败/无密钥
DENY_NOT_WHITELISTED = "NOT_IN_WHITELIST"     # 身份可信但不在名单里

# 只有**显式**声明的生产环境要求白名单。
#
# 为什么不写成"只有 dev/test 才允许无白名单"：本项目的 env 默认是空串（没人声明），
# 而 S1~S4 的 8 个已验收用例就是在"未声明 env"的状态下跑的。若把空串也判成生产，
# 那 8 个用例会从"注入成功"变成"灰度拒绝"——那是**改动了已验收的行为**，不是收紧灰度。
#
# 代价是明确记录的一条边界：生产忘设 APP_ENV 时，这道闸门不生效（与 S1 的数据源合规闸门
# 完全同一条边界）。两道闸门的兜底是同一个东西：发布检查清单。
_PRODUCTION_ENVS = ("prod", "production")


@dataclass(frozen=True)
class Identity:
    """一次请求里能确认的调用方身份。

    `trusted=False` 时 `user_id` 可能是空串（没传），也可能是伪造值（签名不符）——
    两种情况对判定结果一样（不放行），但对排障不一样，所以 `note` 说清是哪一种。
    """

    user_id: str = ""
    trusted: bool = False
    note: str = ""


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    reason: str

    @property
    def denied(self) -> bool:
        return not self.allow


def sign(secret: str, user_id: str, trace_id: str = "") -> str:
    """与 Java 侧同一份算法：`HMAC-SHA256(secret, "<userId>|<traceId>")` 的小写十六进制。"""
    payload = f"{user_id}|{trace_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_identity(
    headers: Mapping[str, str],
    *,
    secret: str,
    trace_id: str = "",
) -> Identity:
    """从请求头还原调用方身份。任何一步不成立都返回 `trusted=False`。"""
    raw_user = str(headers.get(HEADER_USER) or "").strip()
    raw_sig = str(headers.get(HEADER_SIG) or "").strip()

    if not secret:
        return Identity(raw_user, False, "服务未配置内部共享密钥，无法校验身份")
    if not raw_user:
        return Identity("", False, "请求未携带用户身份")
    if not raw_sig:
        return Identity(raw_user, False, "请求未携带身份签名")
    if not raw_user.isdigit():
        return Identity(raw_user, False, "用户身份格式不合法")
    if not hmac.compare_digest(raw_sig.lower(), sign(secret, raw_user, trace_id)):
        return Identity(raw_user, False, "身份签名校验失败")
    return Identity(raw_user, True, "")


def parse_whitelist(raw: str) -> frozenset[str]:
    """逗号分隔的用户 ID 列表。容忍空格与空项（运维从聊天窗口复制时常带空格）。"""
    return frozenset(item.strip() for item in str(raw or "").split(",") if item.strip())


def kill_switch_active(path: str) -> bool:
    """运行时急停：文件存在即关闭。

    用文件而不是"再设一个环境变量"，是因为环境变量改不了运行中的进程——
    而急停的全部意义就是"不用重启、下一分钟就停"。`stat` 一次的成本可以忽略。
    """
    if not path:
        return False
    try:
        return Path(path).exists()
    except OSError:                     # 权限/路径非法时按"没开启急停"处理会更危险
        return True


def decide(
    identity: Identity,
    *,
    settings: Settings,
    kill_active: bool | None = None,
    whitelist: frozenset[str] | None = None,
) -> GateDecision:
    """纯判定：给定身份与配置，回答"这次要不要查天气"。不读环境、不写日志。

    顺序是刻意的：**先看总开关，再看急停，最后才看身份**。
    这样"功能没开"永远是 `INJECT_DISABLED`，不会因为没带身份而报成 `IDENTITY_UNTRUSTED`
    —— 排障时那两个结论指向完全不同的动作（去改配置 / 去看网关）。
    """
    cfg = settings.weather
    if not cfg.inject_on_adjust:
        return GateDecision(False, DENY_INJECT_DISABLED)
    if not cfg.enabled:
        return GateDecision(False, DENY_PROVIDER_DISABLED)
    if kill_active is None:
        kill_active = kill_switch_active(cfg.inject_adjust_kill_file)
    if kill_active:
        return GateDecision(False, DENY_KILLED)

    users = whitelist if whitelist is not None else parse_whitelist(cfg.inject_adjust_users)
    if not users:
        # 显式生产环境上"忘了配"不能等于"对所有人开放"；dev/test/未声明按实例级处理
        if (settings.env or "").strip().lower() in _PRODUCTION_ENVS:
            return GateDecision(False, DENY_NO_WHITELIST)
        return GateDecision(True, GRAY_ALLOWED)

    if not identity.trusted:
        return GateDecision(False, DENY_UNTRUSTED)
    if identity.user_id not in users:
        return GateDecision(False, DENY_NOT_WHITELISTED)
    return GateDecision(True, GRAY_ALLOWED)


def decide_and_log(identity: Identity, *, settings: Settings, trace_id: str = "") -> GateDecision:
    """`decide` + 一行可追踪的日志。

    日志里带 `userId` 与 `trusted`：灰度期最常见的两个问题是"为什么他没开"和
    "为什么他开了"，两个都能靠这一行定位。**不记密钥、不记签名**（签名等于凭据）。
    """
    whitelist = parse_whitelist(settings.weather.inject_adjust_users)
    decision = decide(identity, settings=settings, whitelist=whitelist)
    if decision.allow:
        logger.info(
            "weather_gray_allowed trace=%s userId=%s whitelist=%d",
            trace_id, identity.user_id, len(whitelist),
        )
    else:
        logger.info(
            "weather_gray_denied trace=%s reason=%s userId=%s trusted=%s note=%s whitelist=%d",
            trace_id, decision.reason, identity.user_id or "-",
            identity.trusted, identity.note or "-", len(whitelist),
        )
    return decision
