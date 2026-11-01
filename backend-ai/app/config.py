"""集中配置加载。

原则：所有可变项都可被环境变量覆盖，不在代码里硬编码密钥或服务地址。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass
class LLMSettings:
    provider: str = "mock"            # mock | openai_compatible
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key: str = ""
    timeout_seconds: int = 120
    max_tokens: int = 2048
    temperature: float = 0.7

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"


@dataclass
class Settings:
    app_name: str = "ai-travel-ai-service"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    llm: LLMSettings = field(default_factory=LLMSettings)

    @classmethod
    def load(cls) -> "Settings":
        root = Path(__file__).resolve().parent.parent
        raw = _load_yaml(root / "config" / "config.yaml")
        server = raw.get("server", {}) or {}
        llm_raw = raw.get("llm", {}) or {}
        app_raw = raw.get("app", {}) or {}

        llm = LLMSettings(
            provider=str(_env("LLM_PROVIDER", llm_raw.get("provider", "mock"))),
            base_url=str(_env("LLM_BASE_URL", llm_raw.get("base_url", "https://api.deepseek.com/v1"))),
            model=str(_env("LLM_MODEL", llm_raw.get("model", "deepseek-chat"))),
            api_key=str(_env("LLM_API_KEY", llm_raw.get("api_key", "") or "")),
            timeout_seconds=int(_env("LLM_TIMEOUT_SECONDS", llm_raw.get("timeout_seconds", 120))),
            max_tokens=int(_env("LLM_MAX_TOKENS", llm_raw.get("max_tokens", 2048))),
            temperature=float(_env("LLM_TEMPERATURE", llm_raw.get("temperature", 0.7))),
        )

        return cls(
            app_name=str(app_raw.get("name", "ai-travel-ai-service")),
            version=str(app_raw.get("version", "0.1.0")),
            host=str(_env("AI_SERVICE_HOST", server.get("host", "0.0.0.0"))),
            port=int(_env("AI_SERVICE_PORT", server.get("port", 8000))),
            llm=llm,
        )


settings = Settings.load()
