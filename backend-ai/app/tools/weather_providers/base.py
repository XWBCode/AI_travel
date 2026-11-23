"""Provider 抽象：任何数据源都必须归一化成同一个形状。

## 为什么要有这一层

换数据源（D-03：Open-Meteo → 和风天气）本不该动到 Java 与 Vue。要做到这一点，
**归一化后的中间形状必须只有一份定义**：如果每个 provider 各自拼 FactField，
那么"降水概率是 0–1 还是 0–100"这种差异就会扩散到业务代码里，最终变成"看哪个 provider 生效"的
两套口径——这正是我们要避免的。

所以分工固定：

- **provider**：只负责「发请求 + 把上游字段读成 `NormalizedDay`」。不认识 FactField、
  不认识 `verified`、不认识 `dayIndex`。
- **`weather.py`**：只负责「`NormalizedDay` → 契约里那四个 FactField」。不认识 HTTP、
  不认识 JSON 结构、不认识 unit。

## 单位口径（这里定死，provider 必须服从）

| 字段 | 口径 | 说明 |
|---|---|---|
| `temp_*` | ℃ 裸数字 | 上游给的是 `{value, unit}` 对象，去单位前**必须校验 unit**，不是 ℃ 就当没拿到 |
| `precip_prob` | **0–100** | 和风天气给 0–1 小数（官方 `[0,1]`），乘 100；Open-Meteo 本来就是 0–100 |
| `wind_scale` | **蒲福风级整数** | 和风天气 `wind.scale` 直接给；Open-Meteo 没有，由 km/h 换算 |
| `weather_text` | 中文短词 | 和风天气 `daytime.condition.text` 直接是中文；Open-Meteo 走 WMO 码映射 |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class NormalizedDay:
    """一天的天气，已剔除所有 provider 特有结构。

    每个字段都允许为 None——**None 表示"上游没给/给的不对"，不是 0**。
    调用方必须把 None 变成契约里的「未获取」形态，绝不填 0 顶替：
    填 0 会让"降水概率 0%"和"没拿到降水概率"看起来一样。
    """

    date: date
    weather_text: str | None = None
    temp_max: float | None = None
    temp_min: float | None = None
    temp_mean: float | None = None
    precip_prob: float | None = None  # 0–100
    wind_scale: int | None = None
    wind_speed_ms: float | None = None


@dataclass(frozen=True)
class ProviderResult:
    """一次成功查询的结果。`source` 会原样进 `FactField.source` 与响应的 `source`。"""

    source: str
    attribution: str
    days: list[NormalizedDay]

    def __post_init__(self) -> None:
        # 上游 200 但一天都没解析出来，属于"上游异常"，不能当成"没有天气"返回空列表
        if not self.days:
            raise ProviderFailure("UPSTREAM_ERROR", "天气数据为空")


class ProviderFailure(Exception):
    """可预期的上游/配置失败。

    `reason` 必须是 `weather_reasons` 里的枚举之一——它会被原样透传到前端。
    `hard=True` 表示"继续重试只会更糟"（401/403/429/凭据缺失），熔断器会立刻打开：
    官方明确写了持续的错误请求会被视为攻击并冻结帐号，所以这类错误一次都不重试。
    """

    def __init__(self, reason: str, message: str | None = None, *, hard: bool = False) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message
        self.hard = hard


def num_from(obj: Any, key: str = "value") -> float | None:
    """从 `{value, unit}` 这种对象里取数字。取不到返回 None（不返回 0）。"""
    if not isinstance(obj, dict):
        return None
    raw = obj.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def num(value: Any) -> float | None:
    """裸数字/数字字符串 → float。取不到返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def celsius(obj: Any) -> float | None:
    """取摄氏温度。unit 不是 ℃ 就当没拿到——**不做静默换算**。

    静默换算的问题不在于算错，而在于"算对了也看不出来"：一旦上游把 unit 改成 °F，
    我们会安静地展示一个被猜过的值。宁可降级成"暂无数据"。
    """
    if not isinstance(obj, dict):
        return None
    unit = obj.get("unit")
    if unit is not None and str(unit).strip() not in ("°C", "℃"):
        return None
    return num_from(obj)
