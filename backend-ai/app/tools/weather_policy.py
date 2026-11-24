"""天气数据源的**合规策略**：哪些 provider 允许在哪些环境启动。

这不是代码风格问题，是许可条款问题。

Open-Meteo 免费端点明确禁止商业使用，数据以 CC BY 4.0 发布（须署名）。
仓库里保留它只是为了 CI / 本地无凭据联调——它的价值是「证明契约确实与数据源解耦」，
不是「一个可以随手打开的备用源」。

一旦它被配到生产，后果很特殊：**用户看不到任何异常**。天气照常显示、
温度看起来也对，只有我们知道这是在无授权商用别人的免费端点。
所以这种错误不能靠日志、不能靠告警，只能让进程**起不来**——
启动失败是唯一一个不会被忽略的信号。

判定规则（fail-closed）：
    只有显式声明成 dev / test 的环境才允许 open-meteo。
    环境未声明或写成不认识的值（如 `prd` 这种拼错）一律**拒绝**，
    而不是「看不懂就当 dev」。理由很直接：默认放行的开关迟早会被绕过。
"""

from __future__ import annotations

# 允许使用 Open-Meteo 的环境，必须显式声明
ENVS_ALLOWING_OPEN_METEO = ("dev", "test")

# 生产环境应该用的 provider
PROD_PROVIDER_HINT = "qweather"

# 环境变量名。刻意**不**从 config.yaml 读默认值：
# 那个文件随产物一起进生产，在里面写 env: dev 等于把这道闸门关掉。
#
# 优先 APP_ENV；缺失时由 config.py 回退读 SPRING_PROFILES_ACTIVE（见那边的注释）。
# 两者都没有 → 空串 → 视为「没人声明过环境」→ 不放行。
ENV_VAR = "APP_ENV"

# provider 取值的不同写法统一到注册表用的那个，防止换个拼法绕过检查
# （"open_meteo" / "openmeteo" 最后都会被 get_provider 判为未知源，
#   但未知源是**请求期**降级，不是启动期失败，所以必须在这里就拦住）
_PROVIDER_ALIASES = {
    "open-meteo": "open-meteo",
    "open_meteo": "open-meteo",
    "openmeteo": "open-meteo",
    "open meteo": "open-meteo",
}


class WeatherPolicyError(RuntimeError):
    """配置违反了数据源使用条款。启动期抛出，不允许降级继续运行。"""


def canonical_provider(provider: str | None) -> str:
    """把 provider 写法归一化。未知取值原样返回（交由注册表/编排层处理）。"""
    key = (provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


def is_open_meteo(provider: str | None) -> bool:
    return canonical_provider(provider) == "open-meteo"


def normalize_env(env: str | None) -> str:
    return (env or "").strip().lower()


def split_envs(env: str | None) -> list[str]:
    """环境值可以写多个（与 SPRING_PROFILES_ACTIVE 的逗号分隔一致）。

    两端必须用同一套拆法：Java 侧读的是 `getActiveProfiles()`，本身就是多个值的集合。
    如果 Python 把 `dev,test` 当成一个整体去比对，就会出现
    「Java 认为允许、Python 认为不允许」的分叉。
    """
    return [part for part in (normalize_env(env).split(",")) if part.strip()]


def open_meteo_allowed(env: str | None) -> bool:
    """当前环境是否允许 Open-Meteo。

    fail-closed 的两条：
      1. 一个环境都没声明 → False（不是「默认当 dev」）
      2. 声明了多个时，**全部**都必须落在白名单内 → 只要混进一个 prod 就不放行。
         只要「任一匹配就放行」的话，`dev,prod` 会成为一条干净的绕过路径。
    """
    parts = split_envs(env)
    if not parts:
        return False
    return all(part in ENVS_ALLOWING_OPEN_METEO for part in parts)


def policy_message(env: str | None, provider: str | None) -> str:
    """错误信息。要能让半夜看到日志的人不用翻文档就知道怎么修。

    环境值一律用 repr 显示（带引号）。判定时会 strip 空白，也就是 `' dev '` 视为 dev；
    但日志里仍然要能看出原始值，否则"我明明配了 dev 为什么被拒"会变成一场猜谜
    （真实场景多是 `'dev\\n'`、`'dev '` 这类肉眼看不见的差异）。
    """
    return (
        f"配置被拒绝：天气数据源 {provider!r} 只能在 "
        f"{' / '.join(ENVS_ALLOWING_OPEN_METEO)} 环境使用。"
        f"\n  当前环境：{ENV_VAR}={env!r}"
        f"\n  原因：Open-Meteo 免费端点禁止商业使用（数据 CC BY 4.0，须署名），"
        f"生产使用会违反其条款；且这种违规不会产生任何用户可见的异常，"
        f"所以只能在启动期阻断。"
        f"\n  生产请设 WEATHER_PROVIDER={PROD_PROVIDER_HINT}（和风天气）。"
        f"\n  本地联调请显式设 {ENV_VAR}=dev 或 {ENV_VAR}=test。"
    )


def assert_provider_allowed(env: str | None, provider: str | None) -> None:
    """启动期闸门。不满足即抛 WeatherPolicyError，进程不应起来。"""
    if not is_open_meteo(provider):
        return
    if open_meteo_allowed(env):
        return
    raise WeatherPolicyError(policy_message(env, provider))
