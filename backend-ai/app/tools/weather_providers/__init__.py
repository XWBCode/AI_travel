"""provider 注册表。

新增数据源只改这一个文件 + 一个新模块，`weather.py`（编排 / 归一化 / 降级）不动。
这不是抽象洁癖：D-03 之后我们已经有两条真实路径（和风天气生产、Open-Meteo 联调），
如果每加一个源都要动编排层，那"契约和数据源解耦"就只是说法。
"""

from __future__ import annotations

from types import ModuleType

from app.tools.weather_providers import open_meteo, qweather

# 配置里允许的取值（`weather.provider`）
PROVIDER_NONE = "none"
PROVIDER_QWEATHER = "qweather"
PROVIDER_OPEN_METEO = "open-meteo"

_REGISTRY: dict[str, ModuleType] = {
    PROVIDER_QWEATHER: qweather,
    PROVIDER_OPEN_METEO: open_meteo,
}

SUPPORTED = tuple(_REGISTRY.keys())


def get_provider(name: str) -> ModuleType | None:
    """取 provider 模块。未启用/未知取值返回 None（调用方降级为 PROVIDER_DISABLED）。"""
    key = (name or "").strip().lower()
    return _REGISTRY.get(key)


__all__ = [
    "PROVIDER_NONE",
    "PROVIDER_QWEATHER",
    "PROVIDER_OPEN_METEO",
    "SUPPORTED",
    "get_provider",
    "open_meteo",
    "qweather",
]
