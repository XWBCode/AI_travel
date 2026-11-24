"""集中配置加载。

原则：所有可变项都可被环境变量覆盖，不在代码里硬编码密钥或服务地址。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.tools import weather_policy


def _load_yaml(path: Path) -> dict[str, Any]:
    """极简加载：只支持本项目用到的两层结构，避免为一个配置文件引入 pyyaml 依赖。"""
    import yaml  # 本地依赖，requirements 中已声明

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env(key: str, default: Any) -> Any:
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class LLMSettings:
    provider: str = "mock"            # mock | openai_compatible
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key: str = ""
    timeout_seconds: int = 120
    max_tokens: int = 2048
    temperature: float = 0.7
    # 计划生成时用服务商的严格 JSON 模式；自建/代理服务不兼容时置 false
    json_mode: bool = True

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"


@dataclass
class WeatherSettings:
    """天气数据源配置。

    `provider` 默认 **none**：D-03 定案后生产用和风天气，但凭据不在仓库里，
    所以默认值必须是"关"，不能因为"我们已经接了商用源"就默认启用——
    没有凭据的默认启用只会让每个部署都返回一次 401，还可能触发对方的风控。

    provider 取值：`none` | `qweather` | `open-meteo`
    （`open-meteo` 仅用于 CI/本地无凭据联调，其免费端点禁商用且要求 CC BY 署名）
    """

    provider: str = "none"            # none | qweather | open-meteo
    timeout_seconds: float = 3.0
    connect_timeout_seconds: float = 2.0
    # 官方"逐天预报推荐缓存 1–6 小时"取 1 小时；另受"次日 00:10 必须失效"约束
    cache_ttl_minutes: int = 60
    # 熔断：连续软失败次数 / 冷却秒数（硬错误直接进最长冷却，不看这两个值）
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 300.0

    # ===== M3-2：生成期天气注入 =====
    # 默认 **false**。理由与 `provider` 默认 `none` 完全一样：外部依赖没被显式打开，
    # 就不该在生成链路里悄悄生效。关闭时生成链路的行为与 M3-2 之前逐字相同。
    inject_on_generation: bool = False
    # 注入阶段的硬预算。超时用已完成的部分，一个城市都没成就当没天气（见 weather_inject）
    inject_timeout_ms: int = 2000
    # 一次最多查几个城市。上限是额度与延迟的折中（D4：并发 3）
    inject_max_cities: int = 3
    # 生成期进程内短缓存（D5：用户连点"重新生成"不该重复打上游）。
    # **不动** M3-1 的 Java 侧缓存，这是另加的一层，只管生成链路
    inject_cache_seconds: int = 60

    # ===== M3-3：调整期天气注入 =====
    # 与生成链路分开一个开关：两条链路的延迟形态不同（生成是 SSE，调整是同步 JSON），
    # 关掉其中一条不该连带影响另一条。默认 **false**，理由同上。
    inject_on_adjust: bool = False
    # 调整链路的硬预算，比生成期小：同步请求没有 SSE 的缓冲，天气耗时 100%
    # 落在用户等白屏的时间里，"改个备注"被拖成 2 秒是不可接受的。
    # 这是**整次注入的总预算**，不是每个城市各 1200ms。
    inject_adjust_timeout_ms: int = 1200

    # ===== 和风天气凭据（D-03）=====
    # API Host 是**每个开发者的专属域名**，从控制台-设置复制；用错直接 403 INVALID HOST
    qweather_api_host: str = ""
    qweather_developer_id: str = ""     # Q 开头 10 位，控制台-设置
    qweather_project_id: str = ""       # 控制台-项目管理
    qweather_key_id: str = ""           # 凭据ID，控制台-项目管理
    # 私钥：优先用文件路径（不入仓库、不进日志）；CI 里可用内联内容
    qweather_private_key_path: str = ""
    qweather_private_key_pem: str = field(default="", repr=False)

    @property
    def enabled(self) -> bool:
        return self.provider not in ("", "none", None)

    @property
    def is_qweather(self) -> bool:
        return (self.provider or "").strip().lower() == "qweather"


@dataclass
class Settings:
    app_name: str = "ai-travel-ai-service"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    # 运行环境：dev | test | prod（由 APP_ENV 显式声明）。
    # 默认空串而不是 dev：空串代表「没人声明过」，用于让数据源合规闸门 fail-closed。
    # 具体判定见 app/tools/weather_policy.py。
    env: str = ""
    llm: LLMSettings = field(default_factory=LLMSettings)
    weather: WeatherSettings = field(default_factory=WeatherSettings)

    @classmethod
    def load(cls) -> "Settings":
        root = Path(__file__).resolve().parent.parent
        raw = _load_yaml(root / "config" / "config.yaml")
        server = raw.get("server", {}) or {}
        llm_raw = raw.get("llm", {}) or {}
        app_raw = raw.get("app", {}) or {}
        weather_raw = raw.get("weather", {}) or {}

        llm = LLMSettings(
            provider=str(_env("LLM_PROVIDER", llm_raw.get("provider", "mock"))),
            base_url=str(_env("LLM_BASE_URL", llm_raw.get("base_url", "https://api.deepseek.com/v1"))),
            model=str(_env("LLM_MODEL", llm_raw.get("model", "deepseek-chat"))),
            api_key=str(_env("LLM_API_KEY", llm_raw.get("api_key", "") or "")),
            timeout_seconds=int(_env("LLM_TIMEOUT_SECONDS", llm_raw.get("timeout_seconds", 120))),
            max_tokens=int(_env("LLM_MAX_TOKENS", llm_raw.get("max_tokens", 2048))),
            temperature=float(_env("LLM_TEMPERATURE", llm_raw.get("temperature", 0.7))),
            json_mode=_env_bool("LLM_JSON_MODE", bool(llm_raw.get("json_mode", True))),
        )

        weather = WeatherSettings(
            provider=str(_env("WEATHER_PROVIDER", weather_raw.get("provider", "none"))),
            timeout_seconds=float(_env("WEATHER_TIMEOUT_SECONDS", weather_raw.get("timeout_seconds", 3.0))),
            connect_timeout_seconds=float(
                _env("WEATHER_CONNECT_TIMEOUT_SECONDS", weather_raw.get("connect_timeout_seconds", 2.0))
            ),
            cache_ttl_minutes=int(_env("WEATHER_CACHE_TTL_MINUTES", weather_raw.get("cache_ttl_minutes", 60))),
            circuit_failure_threshold=int(
                _env("WEATHER_CIRCUIT_FAILURE_THRESHOLD", weather_raw.get("circuit_failure_threshold", 3))
            ),
            circuit_cooldown_seconds=float(
                _env("WEATHER_CIRCUIT_COOLDOWN_SECONDS", weather_raw.get("circuit_cooldown_seconds", 300))
            ),
            inject_on_generation=_env_bool(
                "WEATHER_INJECT_ON_GENERATION", bool(weather_raw.get("inject_on_generation", False))
            ),
            inject_timeout_ms=int(
                _env("WEATHER_INJECT_TIMEOUT_MS", weather_raw.get("inject_timeout_ms", 2000))
            ),
            inject_max_cities=int(
                _env("WEATHER_INJECT_MAX_CITIES", weather_raw.get("inject_max_cities", 3))
            ),
            inject_cache_seconds=int(
                _env("WEATHER_INJECT_CACHE_SECONDS", weather_raw.get("inject_cache_seconds", 60))
            ),
            inject_on_adjust=_env_bool(
                "WEATHER_INJECT_ON_ADJUST", bool(weather_raw.get("inject_on_adjust", False))
            ),
            inject_adjust_timeout_ms=int(
                _env("WEATHER_INJECT_ADJUST_TIMEOUT_MS",
                     weather_raw.get("inject_adjust_timeout_ms", 1200))
            ),
            qweather_api_host=str(
                _env("QWEATHER_API_HOST", weather_raw.get("qweather_api_host", "")) or ""
            ),
            qweather_developer_id=str(
                _env("QWEATHER_DEVELOPER_ID", weather_raw.get("qweather_developer_id", "")) or ""
            ),
            qweather_project_id=str(
                _env("QWEATHER_PROJECT_ID", weather_raw.get("qweather_project_id", "")) or ""
            ),
            qweather_key_id=str(_env("QWEATHER_KEY_ID", weather_raw.get("qweather_key_id", "")) or ""),
            qweather_private_key_path=str(
                _env("QWEATHER_PRIVATE_KEY_PATH", weather_raw.get("qweather_private_key_path", "")) or ""
            ),
            # 私钥内容只允许走环境变量/密钥文件，不读 config.yaml——那个文件是入库的
            qweather_private_key_pem=str(_env("QWEATHER_PRIVATE_KEY_PEM", "") or ""),
        )

        # 环境标识。只从环境变量读，**不**给 yaml 默认值：config.yaml 随产物进生产，
        # 在那里写 env: dev 会让合规闸门在生产上静默失效。
        #
        # 回退到 SPRING_PROFILES_ACTIVE 是刻意的：本项目「当前是哪个环境」的唯一正式旋钮
        # 是 06 规范里那一个（Java 侧用），如果再要求部署方额外记得设 APP_ENV，
        # 迟早出现「Java 认为自己是 prod、Python 认为自己是 dev」的分叉——
        # 两边闸门判定不一致，比只有一道闸门更危险。
        # 两者都设时 APP_ENV 优先；一个都没设 → 空串 → 按未声明处理（fail-closed）。
        env = str(_env("APP_ENV", _env("SPRING_PROFILES_ACTIVE", ""))).strip()

        # 数据源合规闸门：违反使用条款的配置必须让进程起不来。
        # 放在 load() 里而不是 lifespan 里，是为了让错误出现在「导入配置」这一步——
        # 越早失败，越不可能出现「服务已启动、只是天气不对」这种半可用状态。
        weather_policy.assert_provider_allowed(env, weather.provider)

        return cls(
            app_name=str(app_raw.get("name", "ai-travel-ai-service")),
            version=str(app_raw.get("version", "0.1.0")),
            host=str(_env("AI_SERVICE_HOST", server.get("host", "0.0.0.0"))),
            port=int(_env("AI_SERVICE_PORT", server.get("port", 8000))),
            env=env,
            llm=llm,
            weather=weather,
        )


settings = Settings.load()
