"""Open-Meteo provider（开发 / CI 用，非生产）。

D-03 定案：生产数据和风天气，Open-Meteo **保留**但只作为 `provider=open-meteo` 的开发与
无凭据联调选项。保留理由不是"多一个选择更好"，而是：CI 里没有和风天气凭据，删掉它
天气链路的联调就会全部退化成 mock，而 mock 只能证明代码跑通，证明不了上游还长这样。

## 署名（CC BY 4.0）

`attribution` 必须逐字展示在前端，这不是礼貌问题，是许可条件。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.tools.weather_providers.base import (
    NormalizedDay,
    ProviderFailure,
    ProviderResult,
    num,
)
from app.tools.weather_reasons import R_UPSTREAM_ERROR, R_UPSTREAM_TIMEOUT

UPSTREAM_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "OPEN_METEO"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# WMO 4677 天气码 → 中文。覆盖 Open-Meteo forecast 会返回的全部取值，
# 未收录的码一律降级成"未知"，不猜。
#
# 这张表**只属于 Open-Meteo**：和风天气的 `condition.text` 直接给中文，不需要映射。
# 换源时它被限制在本文件内，而不是留在公共归一化层里让两个 provider 抢着用。
_WMO_TEXT = {
    0: "晴",
    1: "晴间多云",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "强雷阵雨伴冰雹",
}

# 蒲福风级的下界（m/s）。用来把 km/h 换算成"几级风"——和风天气直接给 scale，
# 本换算只服务 Open-Meteo。
_BEAUFORT_LOWER_MS = (0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7)


def weather_text(code: Any) -> str | None:
    try:
        return _WMO_TEXT.get(int(code))
    except (TypeError, ValueError):
        return None


def wind_level(kmh: float) -> int:
    """km/h → 蒲福风级。比直接报 km/h 更符合国内用户对"几级风"的直觉。"""
    ms = kmh / 3.6
    level = 0
    for lower in _BEAUFORT_LOWER_MS:
        if ms >= lower:
            level += 1
    return level


def _pick(daily: dict[str, Any], key: str, i: int) -> float | None:
    series = daily.get(key)
    if not isinstance(series, list) or i >= len(series):
        return None
    return num(series[i])


async def fetch(
    *,
    lat: float,
    lon: float,
    start: date,
    end: date,
    timeout_seconds: float = 3.0,
    connect_timeout_seconds: float = 2.0,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    settings: Any = None,
    now: Any = None,
    clock: Any = None,
    token_provider: Any = None,
) -> ProviderResult:
    """查询 [start, end] 的逐日天气（两个日期都已由调用方裁到窗口内）。

    `clock` / `token_provider` 本 provider 用不到（Open-Meteo 无鉴权），
    但**签名与 qweather 保持一致**：编排层只有一个调用口径，不按 provider 写分支。
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Asia/Shanghai",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(
            (
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "temperature_2m_mean",
                "precipitation_probability_max",
                "wind_speed_10m_max",
            )
        ),
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
            transport=transport,
        )
    try:
        resp = await client.get(UPSTREAM_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.TimeoutException as e:
        raise ProviderFailure(R_UPSTREAM_TIMEOUT) from e
    except httpx.HTTPStatusError as e:
        # 429/5xx 在 Open-Meteo 都是"过一会儿可能好"，不算 hard
        raise ProviderFailure(R_UPSTREAM_ERROR) from e
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderFailure(R_UPSTREAM_ERROR) from e
    finally:
        if owns_client:
            await client.aclose()

    daily = (payload or {}).get("daily") or {}
    times = daily.get("time") or []
    if not times:
        # 上游 200 但结构变了/没数据：这是"上游异常"，不是"没有天气"
        raise ProviderFailure(R_UPSTREAM_ERROR, "天气数据格式异常")

    days: list[NormalizedDay] = []
    for i, raw_day in enumerate(times):
        try:
            day_date = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if not (start <= day_date <= end):
            continue
        wind_kmh = _pick(daily, "wind_speed_10m_max", i)
        days.append(
            NormalizedDay(
                date=day_date,
                weather_text=weather_text(_pick(daily, "weather_code", i)),
                temp_max=_pick(daily, "temperature_2m_max", i),
                temp_min=_pick(daily, "temperature_2m_min", i),
                temp_mean=_pick(daily, "temperature_2m_mean", i),
                precip_prob=_pick(daily, "precipitation_probability_max", i),
                wind_scale=wind_level(wind_kmh) if wind_kmh is not None else None,
                wind_speed_ms=round(wind_kmh / 3.6, 2) if wind_kmh is not None else None,
            )
        )

    return ProviderResult(source=SOURCE, attribution=ATTRIBUTION, days=days)
