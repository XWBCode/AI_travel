"""天气工具层：城市解析 + Open-Meteo 调用 + 归一化成 FactField。

三条硬约束（来自 `03-外部数据源选型与降级规范.md` §4，以及 M3-1 的 PM 批注）：

1. **不造数**。只有真的拿到上游某一天的数据，才把该字段置 `verified=true` / `source=TOOL`。
   缺失就置 `verified=false` / `source=NONE` / `value=null`——契约里这条是强约束
   （`factFieldBase` 的 `allOf` 分支），不是风格问题。
2. **可预期失败不抛异常**。统一 `{"ok": False, "reason": ...}` 返回，由 Java 决定降级文案。
   只有参数本身非法（比如 days 不是正整数）才让路由层返回 422。
3. **不越窗口**。超出 Open-Meteo 预报范围就如实降级，绝不用"历史同期平均"顶上——
   那是看起来最像真数据的假信息。

> 单位约定：上游 `temperature_2m_*` 为 ℃、`precipitation_probability_max` 为 %、
> `wind_speed_10m_max` 默认 km/h。`display` 里带单位，`value` 只放裸数字，两者口径见各字段注释。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("ai-travel.weather")

# 中国无夏令时，固定 +08:00 与 Asia/Shanghai 完全等价；
# 不用 ZoneInfo 是因为 Windows 不带 IANA 时区库（会抛 ZoneInfoNotFoundError），
# 为一个时区引入 tzdata 依赖不划算。上游请求参数仍显式传 `timezone=Asia/Shanghai`，
# 让 Open-Meteo 按当地日历切天。
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
TZ_NAME = "Asia/Shanghai"
COORDS_FILE = Path(__file__).with_name("city_coords.json")

UPSTREAM_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_OPEN_METEO = "OPEN_METEO"
SOURCE_NONE = "NONE"
# CC BY 4.0 要求署名，前端必须逐字展示这一串
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# 免费预报端点约 16 天（含今天）。这个数字变了要同步改 03 文档与前端文案
FORECAST_WINDOW_DAYS = 16
# 03 文档 §6：城市天气预报缓存 30–60 分钟，取下界
CACHE_TTL_MINUTES = 30

# 降级原因。与设计稿 §4.1 的 reason 枚举逐字对应，前端按枚举映射文案
R_NO_START_DATE = "NO_START_DATE"
R_CITY_UNKNOWN = "CITY_UNKNOWN"
R_DATE_OUT_OF_RANGE = "DATE_OUT_OF_RANGE"
R_UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
R_UPSTREAM_ERROR = "UPSTREAM_ERROR"
R_PROVIDER_DISABLED = "PROVIDER_DISABLED"
R_NO_CITY = "NO_CITY"

_MESSAGES = {
    R_NO_START_DATE: "补充出发日期后可查看天气",
    R_CITY_UNKNOWN: "该地点暂无天气数据",
    R_DATE_OUT_OF_RANGE: "出发日期超出预报范围，临近再看",
    R_UPSTREAM_TIMEOUT: "天气服务响应超时",
    R_UPSTREAM_ERROR: "天气服务暂时不可用",
    R_PROVIDER_DISABLED: "天气功能未开启",
    R_NO_CITY: "计划里还没有城市，无法查询天气",
}

# WMO 4677 天气码 → 中文。覆盖 Open-Meteo forecast 会返回的全部取值，
# 未收录的码一律降级成"未知"，不猜
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

# 城市名后缀。按长度倒序匹配，先剥长的（"自治州"不能被"州"吃掉）
_CITY_SUFFIXES = (
    "哈萨克自治州", "蒙古自治州", "藏族自治州", "彝族自治州", "傣族自治州",
    "白族自治州", "苗族自治州", "土家族自治州", "朝鲜族自治州",
    "自治区", "自治州", "自治县", "特别行政区", "地区", "新区", "城区",
    # "省" 只出现在省份写法（"云南省"），城市名不会以它结尾，剥它不会误伤
    "省", "市", "县", "区", "盟", "镇",
)

# 民族名：剥掉"大理白族"这类尾巴才能命中"大理"
_ETHNIC_TOKENS = (
    "维吾尔", "哈萨克", "柯尔克孜", "塔吉克", "蒙古", "朝鲜", "土家", "哈尼",
    "布依", "傈僳", "拉祜", "纳西", "景颇", "仡佬", "侗", "藏", "羌", "彝",
    "苗", "瑶", "壮", "回", "白", "傣", "黎", "佤", "畲", "水", "土", "满",
)


# ---------------------------------------------------------------- 城市解析


@dataclass(frozen=True)
class CityCoord:
    """解析结果。`city` 是真正拿去取坐标的那个城市名，`input` 是用户/计划里的原值。"""

    input: str
    city: str
    lat: float
    lon: float
    note: str = ""

    @property
    def is_alias(self) -> bool:
        return self.city != self.input


@lru_cache(maxsize=1)
def _table() -> tuple[dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    with COORDS_FILE.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw["cities"], raw.get("aliases", {})


@lru_cache(maxsize=1)
def known_cities() -> tuple[str, ...]:
    cities, _ = _table()
    return tuple(cities.keys())


def _strip_suffix(name: str) -> str:
    for suffix in _CITY_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _strip_ethnic(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for token in _ETHNIC_TOKENS:
            if name.endswith(token) and len(name) > len(token):
                name = name[: -len(token)]
                changed = True
    return name


def _lookup(name: str) -> str | None:
    """在表里查一个名字，命中返回表内的规范键名。

    顺序很重要：先精确，再剥后缀，再剥民族名，最后做前缀匹配。
    前缀匹配是为了「成都双流机场」「北京朝阳」这类带地名修饰的写法，
    但它必须放在最后，否则「西双版纳」会被「西」开头的某个城市抢走。
    """
    cities, aliases = _table()
    if not name:
        return None
    if name in cities:
        return name

    stripped = _strip_ethnic(_strip_suffix(name))
    if stripped in cities:
        return stripped

    if name in aliases:
        return aliases[name]["city"]
    if stripped in aliases:
        return aliases[stripped]["city"]

    # 前缀匹配：取最长的命中，且剩余部分不超过 6 字（避免「南京路步行街」这种远距离误命中）
    best: str | None = None
    for key in cities:
        if len(key) >= 2 and name.startswith(key) and len(name) - len(key) <= 6:
            if best is None or len(key) > len(best):
                best = key
    return best


def resolve_city(raw: str | None) -> CityCoord | None:
    """把计划里的城市名解析成坐标。解析不出来返回 None（调用方降级为 CITY_UNKNOWN）。"""
    if not raw:
        return None
    name = str(raw).strip()
    cities, aliases = _table()
    # 计划里有「成都/都江堰」这种多城写法，取第一个能吃到的
    for part in _split_city(name):
        alias = _alias_entry(part, aliases)
        if alias is not None and alias["city"] in cities:
            coord = cities[alias["city"]]
            # 省份/大区映射到省会时，note 说明"按谁代表谁"，不假装精确
            return CityCoord(
                input=name,
                city=alias["city"],
                lat=float(coord["lat"]),
                lon=float(coord["lon"]),
                note=alias.get("note", ""),
            )
        key = _lookup(part)
        if key is None:
            continue
        coord = cities[key]
        return CityCoord(
            input=name,
            city=key,
            lat=float(coord["lat"]),
            lon=float(coord["lon"]),
            note="",
        )
    return None


def _alias_entry(part: str, aliases: dict[str, dict[str, str]]) -> dict[str, str] | None:
    """别名（省 / 大区 / 简称）查询。剥后缀与民族名后再试一次，容纳「云南省」这种写法。"""
    if part in aliases:
        return aliases[part]
    stripped = _strip_ethnic(_strip_suffix(part))
    if stripped in aliases:
        return aliases[stripped]
    return None


def _split_city(name: str) -> list[str]:
    """拆分多城写法：`成都/都江堰`、`成都、都江堰`、`成都-都江堰`。"""
    parts = [name]
    for sep in ("/", "、", ",", "，", "-", "—", "→"):
        expanded: list[str] = []
        for part in parts:
            expanded.extend(part.split(sep))
        parts = expanded
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------- 归一化


def weather_text(code: Any) -> str:
    try:
        return _WMO_TEXT.get(int(code), "未知")
    except (TypeError, ValueError):
        return "未知"


def wind_level(kmh: float) -> int:
    """km/h → 蒲福风级。比直接报 km/h 更符合国内用户对"几级风"的直觉。"""
    ms = kmh / 3.6
    level = 0
    for lower in (0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7):
        if ms >= lower:
            level += 1
    return level


def _number_field(
    value: float | None,
    display: str,
    fetched_at: str | None,
    expires_at: str | None,
    note: str = "",
) -> dict[str, Any]:
    """构造一个数字型 FactField。

    `value is None` 时必须是「未获取」形态（verified=false / source=NONE / 时间为 null），
    否则会违反 `factFieldBase.allOf` 里 verified=true 要求 fetchedAt 的约束。
    """
    if value is None:
        return {
            "value": None,
            "display": display,
            "source": SOURCE_NONE,
            "verified": False,
            "fetchedAt": None,
            "expiresAt": None,
            "note": note,
        }
    return {
        "value": value,
        "display": display,
        "source": "TOOL",
        "verified": True,
        "fetchedAt": fetched_at,
        "expiresAt": expires_at,
        # note 走契约里的 freeText，禁止出现「数字+单位」，所以只放纯文字
        "note": note,
    }


def _pick(daily: dict[str, Any], key: str, i: int) -> float | None:
    series = daily.get(key)
    if not isinstance(series, list) or i >= len(series):
        return None
    raw = series[i]
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def build_day(
    day: date,
    day_index: int,
    coord: CityCoord,
    daily: dict[str, Any],
    i: int,
    fetched_at: str,
    expires_at: str,
) -> dict[str, Any]:
    t_max = _pick(daily, "temperature_2m_max", i)
    t_min = _pick(daily, "temperature_2m_min", i)
    t_mean = _pick(daily, "temperature_2m_mean", i)
    rain = _pick(daily, "precipitation_probability_max", i)
    wind = _pick(daily, "wind_speed_10m_max", i)
    code = _pick(daily, "weather_code", i)

    if t_max is not None and t_min is not None:
        temp_display = f"{round(t_min):d}–{round(t_max):d}℃"
        # value 取日均温：单个数字用于排序/比较，区间放 display，两者语义不同但都来自上游
        temp_value = round(t_mean if t_mean is not None else (t_max + t_min) / 2, 1)
    else:
        temp_display = "暂无数据"
        temp_value = None

    if rain is not None:
        rain_display = f"降水概率 {round(rain):d}%"
    else:
        rain_display = "暂无数据"

    if wind is not None:
        wind_display = f"{wind_level(wind)}级风"
    else:
        wind_display = "暂无数据"

    note = coord.note if coord.is_alias else ""
    return {
        "dayIndex": day_index,
        "date": day.isoformat(),
        "city": coord.input,
        "resolvedCity": coord.city,
        "weather": {
            "value": weather_text(code),
            "display": weather_text(code),
            "source": "TOOL" if code is not None else SOURCE_NONE,
            "verified": code is not None,
            "fetchedAt": fetched_at if code is not None else None,
            "expiresAt": expires_at if code is not None else None,
            "note": note,
        },
        "temperature": _number_field(temp_value, temp_display, fetched_at, expires_at, note),
        "precipitation": _number_field(rain, rain_display, fetched_at, expires_at, note),
        "wind": _number_field(wind, wind_display, fetched_at, expires_at, note),
    }


# ---------------------------------------------------------------- 上游调用


def _fail(reason: str, message: str | None = None) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "message": message or _MESSAGES.get(reason, "天气暂不可用")}


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def forecast_window(now: datetime) -> tuple[date, date]:
    """可查询窗口：[今天, 今天+15]。超出就降级，不做气候平均替代。"""
    today = now.astimezone(TZ).date()
    return today, today + timedelta(days=FORECAST_WINDOW_DAYS - 1)


async def fetch_forecast(
    city: str | None,
    start_date: str | date | None,
    days: int,
    *,
    provider: str = "open-meteo",
    timeout_seconds: float = 3.0,
    connect_timeout_seconds: float = 2.0,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """查询逐日天气。返回 `{"ok": True, ...}` 或 `{"ok": False, "reason": ..., "message": ...}`。

    参数：
    - `city` 计划里的城市（可空，空则 `NO_CITY`）
    - `start_date` 出发日期（ISO 字符串或 date；空则 `NO_START_DATE`，本层也判一次，
      避免 Java 漏判时拿着 today 去查——那就是 B 方案，已被否掉）
    - `days` 需要几天（1..16）
    """
    if provider in ("", "none", None):
        return _fail(R_PROVIDER_DISABLED)
    if not city:
        return _fail(R_NO_CITY)

    now = now or datetime.now(TZ)

    if not start_date:
        # 刻意不兜底成"今天"：没有出发日期就查不出"哪天天气"，兜底等于编造
        return _fail(R_NO_START_DATE)

    try:
        start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    except ValueError:
        return _fail(R_DATE_OUT_OF_RANGE, "出发日期格式不正确")

    coord = resolve_city(city)
    if coord is None:
        return _fail(R_CITY_UNKNOWN, f"暂无「{city}」的天气数据")

    window_start, window_end = forecast_window(now)
    end = start + timedelta(days=max(days, 1) - 1)
    want_start = max(start, window_start)
    want_end = min(end, window_end)
    if want_end < want_start:
        if end < window_start:
            return _fail(R_DATE_OUT_OF_RANGE, "行程日期已过去")
        return _fail(R_DATE_OUT_OF_RANGE)

    params = {
        "latitude": coord.lat,
        "longitude": coord.lon,
        "timezone": TZ_NAME,
        "start_date": want_start.isoformat(),
        "end_date": want_end.isoformat(),
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
    except httpx.TimeoutException:
        logger.warning("weather upstream timeout city=%s start=%s", coord.city, want_start)
        return _fail(R_UPSTREAM_TIMEOUT)
    except httpx.HTTPStatusError as e:
        logger.warning("weather upstream status=%s city=%s", e.response.status_code, coord.city)
        return _fail(R_UPSTREAM_ERROR)
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("weather upstream error city=%s err=%s", coord.city, type(e).__name__)
        return _fail(R_UPSTREAM_ERROR)
    finally:
        if owns_client:
            await client.aclose()

    daily = (payload or {}).get("daily") or {}
    times = daily.get("time") or []
    if not times:
        # 上游 200 但结构变了/没数据：这是"上游异常"，不是"没有天气"
        logger.warning("weather upstream empty daily city=%s keys=%s", coord.city, list(daily))
        return _fail(R_UPSTREAM_ERROR, "天气数据格式异常")

    fetched_at = _iso(now)
    expires_at = _iso(now + timedelta(minutes=CACHE_TTL_MINUTES))

    out_days: list[dict[str, Any]] = []
    for i, raw_day in enumerate(times):
        try:
            day_date = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if not (want_start <= day_date <= want_end):
            continue
        # dayIndex 相对整段行程的第 1 天，而不是相对本次返回的第 1 天
        day_index = (day_date - start).days + 1
        out_days.append(build_day(day_date, day_index, coord, daily, i, fetched_at, expires_at))

    if not out_days:
        return _fail(R_UPSTREAM_ERROR, "天气数据为空")

    note = None
    if want_start != start:
        note = "行程中已过去的日期不提供天气"
    if want_end != end:
        note = (note + "；" if note else "") + "超出预报范围的日期不提供天气"

    return {
        "ok": True,
        "source": SOURCE_OPEN_METEO,
        "attribution": ATTRIBUTION,
        "city": coord.input,
        "resolvedCity": coord.city,
        "fetchedAt": fetched_at,
        "expiresAt": expires_at,
        "ttlMinutes": CACHE_TTL_MINUTES,
        "note": note,
        "days": out_days,
    }
