"""天气编排层：城市解析 + 窗口裁剪 + provider 调度 + 归一化成 FactField。

> 数据源已经与这里解耦：具体"和风天气 / Open-Meteo"的 HTTP、JSON 结构、单位、
> 中文映射都在 `app/tools/weather_providers/` 里。本文件不认识任何上游字段名，
> 只认识 `NormalizedDay`。换源时这里**一行都不用改**。

## 三条硬约束（来自 `03-外部数据源选型与降级规范.md` §4，以及 M3-1 的 PM 批注）

1. **不造数**。只有真的拿到上游某一天的数据，才把该字段置 `verified=true` / `source=TOOL`。
   缺失就置 `verified=false` / `source=NONE` / `value=null`——契约里这条是强约束
   （`factFieldBase` 的 `allOf` 分支），不是风格问题。**绝不填 0 顶替**：
   填 0 会让"降水概率 0%"和"没拿到降水概率"长得一模一样。
2. **可预期失败不抛异常**。统一 `{"ok": False, "reason": ...}` 返回，由 Java 决定降级文案。
   只有参数本身非法（比如 days 不是正整数）才让路由层返回 422。
3. **不越窗口**。超出预报范围就如实降级，绝不用"历史同期平均"顶上——
   那是看起来最像真数据的假信息。

## 预报窗口（D-03 后统一为 10 天）

和风天气官方上限是 10 天，Open-Meteo 免费端点约 16 天。**统一取 10**：
前后端口径只有一个数字，比"每个源各自一个上限"少一整类"前端说能看、后端说超范围"的错。

## 缓存过期时间

`min(now + 60min, 次日 00:10)`。第二个边界不是洁癖——和风天气官方缓存文档点名了这个坑：
23:00 缓存了"明天起 3 天"，次日 00:10 之后如果不失效，用户看到的仍然是"明天"，**整体错一天**。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.tools import weather_circuit
from app.tools.weather_providers import PROVIDER_NONE, get_provider
from app.tools.weather_providers.base import NormalizedDay, ProviderFailure, ProviderResult
from app.tools.weather_reasons import (  # noqa: F401  —— R_* 由本模块再导出，历史引用点不动
    ALL_REASONS,
    MESSAGES,
    R_CITY_UNKNOWN,
    R_CIRCUIT_OPEN,
    R_DATE_OUT_OF_RANGE,
    R_NO_CITY,
    R_NO_START_DATE,
    R_PROVIDER_DISABLED,
    R_UPSTREAM_ERROR,
    R_UPSTREAM_TIMEOUT,
    fail as _fail,
)

logger = logging.getLogger("ai-travel.weather")

# 中国无夏令时，固定 +08:00 与 Asia/Shanghai 完全等价；
# 不用 ZoneInfo 是因为 Windows 不带 IANA 时区库（会抛 ZoneInfoNotFoundError），
# 为一个时区引入 tzdata 依赖不划算。
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
TZ_NAME = "Asia/Shanghai"
COORDS_FILE = Path(__file__).with_name("city_coords.json")

SOURCE_NONE = "NONE"

# 统一预报窗口：和风天气上限 10 天。这个数字变了要同步改 04 契约示例、Java、前端文案
FORECAST_WINDOW_DAYS = 10
# 官方"逐天预报推荐缓存 1–6 小时"，取 1 小时；另受"次日 00:10"硬边界约束（见模块文档）
CACHE_TTL_MINUTES = 60
# 跨天失效的零点偏移：留 10 分钟给上游更新 00:00 那一班数据
CROSS_DAY_CUTOFF = time(0, 10)

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
    """把计划里的城市名解析成坐标。解析不出来返回 None（调用方降级为 CITY_UNKNOWN）。

    自带坐标表而不是调 GeoAPI，除了网络可达性，还有个合规理由：和风天气官方
    「缓存你的数据」页明确写了 GeoAPI 数据**禁止缓存/批量存储/建索引**（版权方要求）。
    我们只存坐标、不存地理名称数据，恰好不踩这条线。
    """
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


def build_day(
    day: NormalizedDay,
    day_index: int,
    coord: CityCoord,
    fetched_at: str,
    expires_at: str,
    source: str,
) -> dict[str, Any]:
    """`NormalizedDay` → 契约里的一天。**这是唯一的 FactField 拼装点**，两个 provider 共用。"""
    if day.temp_max is not None and day.temp_min is not None:
        temp_display = f"{round(day.temp_min):d}–{round(day.temp_max):d}℃"
        # value 取日均温：单个数字用于排序/比较，区间放 display，
        # 两者语义不同但都来自上游（有 Avg 就用 Avg，没有才用最高最低的平均）
        temp_mean = day.temp_mean
        if temp_mean is None:
            temp_mean = (day.temp_max + day.temp_min) / 2
        temp_value = round(temp_mean, 1)
    else:
        # 缺一侧就整组降级：只显示"最高 20℃"会让人以为是全天气温
        temp_display = "暂无数据"
        temp_value = None

    rain_display = f"降水概率 {round(day.precip_prob):d}%" if day.precip_prob is not None else "暂无数据"

    if day.wind_scale is not None:
        wind_display = f"{day.wind_scale}级风"
    elif day.wind_speed_ms is not None:
        # 没有风级才退回风速，绝不自己再算一遍蒲福级（两套口径会打架）
        wind_display = f"{day.wind_speed_ms:.0f}m/s"
    else:
        wind_display = "暂无数据"

    note = coord.note if coord.is_alias else ""
    has_text = bool(day.weather_text)
    return {
        "dayIndex": day_index,
        "date": day.date.isoformat(),
        "city": coord.input,
        "resolvedCity": coord.city,
        "weather": {
            "value": day.weather_text if has_text else None,
            "display": day.weather_text if has_text else "暂无数据",
            "source": "TOOL" if has_text else SOURCE_NONE,
            "verified": has_text,
            "fetchedAt": fetched_at if has_text else None,
            "expiresAt": expires_at if has_text else None,
            "note": note,
        },
        "temperature": _number_field(temp_value, temp_display, fetched_at, expires_at, note),
        "precipitation": _number_field(day.precip_prob, rain_display, fetched_at, expires_at, note),
        # wind.value 用风级（整数），与 display 的"几级风"同一口径；没有风级才用 m/s
        "wind": _number_field(
            day.wind_scale if day.wind_scale is not None else day.wind_speed_ms,
            wind_display,
            fetched_at,
            expires_at,
            note,
        ),
    }


# ---------------------------------------------------------------- 时间与窗口


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def forecast_window(now: datetime) -> tuple[date, date]:
    """可查询窗口：[今天, 今天+9]。超出就降级，不做气候平均替代。"""
    today = now.astimezone(TZ).date()
    return today, today + timedelta(days=FORECAST_WINDOW_DAYS - 1)


def expiry_of(now: datetime, ttl_minutes: int = CACHE_TTL_MINUTES) -> datetime:
    """缓存到期时间 = min(now + ttl, 次日 00:10)。

    第二个边界是为了「跨天」：官方缓存文档点名 23:00 缓存"明天起 3 天"、
    次日不刷新会整体错一天的坑。把到期时间钉在次日 00:10 之后，key 又会随自然日变化，
    两层都堵住。
    """
    local_now = now.astimezone(TZ)
    by_ttl = local_now + timedelta(minutes=ttl_minutes)
    next_midnight = datetime.combine(local_now.date() + timedelta(days=1), CROSS_DAY_CUTOFF, tzinfo=TZ)
    return min(by_ttl, next_midnight)


# ---------------------------------------------------------------- 上游调用


async def fetch_forecast(
    city: str | None,
    start_date: str | date | None,
    days: int,
    *,
    provider: str = PROVIDER_NONE,
    timeout_seconds: float = 3.0,
    connect_timeout_seconds: float = 2.0,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    settings: Any = None,
    clock: Any = None,
    token_provider: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """查询逐日天气。返回 `{"ok": True, ...}` 或 `{"ok": False, "reason": ..., "message": ...}`。

    参数：
    - `city` 计划里的城市（可空，空则 `NO_CITY`）
    - `start_date` 出发日期（ISO 字符串或 date；空则 `NO_START_DATE`，本层也判一次，
      避免 Java 漏判时拿着 today 去查——那就是 B 方案，已被否掉）
    - `days` 需要几天（1..10）
    - `settings` 传给 provider 的配置对象（和风天气要用它取 host/凭据）
    """
    module = get_provider(provider)
    if module is None:
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

    breaker = weather_circuit.breaker_for(
        provider,
        failure_threshold=int(getattr(settings, "circuit_failure_threshold", 3) or 3),
        cooldown_seconds=float(getattr(settings, "circuit_cooldown_seconds", 300.0) or 300.0),
    )
    now_ts = now.timestamp()
    if not breaker.allow(now_ts):
        # 熔断期间不发请求：官方明确写了持续错误请求可能冻结账号。
        # reason 用 CIRCUIT_OPEN 而不是 UPSTREAM_ERROR：**这里根本没出网**，
        # 上游这一轮有没有好都不知道。报成"上游错误"会让运维去查一个没被碰过的接口
        # （详见 weather_reasons 模块文档）
        logger.warning("天气熔断中，本次未访问上游 provider=%s city=%s", provider, coord.city)
        return _fail(R_CIRCUIT_OPEN)

    try:
        result = await module.fetch(
            lat=coord.lat,
            lon=coord.lon,
            start=want_start,
            end=want_end,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            client=client,
            transport=transport,
            settings=settings,
            now=now,
            clock=clock,
            token_provider=token_provider,
        )
    except ProviderFailure as e:
        breaker.record_failure(now_ts, hard=e.hard)
        logger.warning(
            "天气上游失败 provider=%s city=%s reason=%s hard=%s", provider, coord.city, e.reason, e.hard
        )
        return _fail(e.reason, e.message)
    except Exception:  # noqa: BLE001 —— 上游/解析的任何意外都不能穿透到路由层
        breaker.record_failure(now_ts)
        logger.exception("天气上游未预期异常 provider=%s city=%s", provider, coord.city)
        return _fail(R_UPSTREAM_ERROR)
    breaker.record_success()

    return _assemble(result, coord, start, end, want_start, want_end, now, settings)


def _assemble(
    result: ProviderResult,
    coord: CityCoord,
    start: date,
    end: date,
    want_start: date,
    want_end: date,
    now: datetime,
    settings: Any,
) -> dict[str, Any]:
    ttl_minutes = int(getattr(settings, "cache_ttl_minutes", CACHE_TTL_MINUTES) or CACHE_TTL_MINUTES)
    fetched_at = _iso(now)
    expires_at = _iso(expiry_of(now, ttl_minutes))

    out_days: list[dict[str, Any]] = []
    for day in result.days:
        if not (want_start <= day.date <= want_end):
            continue
        # dayIndex 相对整段行程的第 1 天，而不是相对本次返回的第 1 天
        day_index = (day.date - start).days + 1
        out_days.append(build_day(day, day_index, coord, fetched_at, expires_at, result.source))

    if not out_days:
        return _fail(R_UPSTREAM_ERROR, "天气数据为空")

    # note 只说"哪一段没给"，且不写数字（note 走 freeText，禁止"数字+单位"）
    note_parts: list[str] = []
    if want_start != start:
        note_parts.append("行程中已过去的日期不提供天气")
    if want_end != end:
        note_parts.append("超出预报范围的日期不提供天气")
    return {
        "ok": True,
        "source": result.source,
        "attribution": result.attribution,
        "city": coord.input,
        "resolvedCity": coord.city,
        "fetchedAt": fetched_at,
        "expiresAt": expires_at,
        "ttlMinutes": ttl_minutes,
        "note": "；".join(note_parts) if note_parts else None,
        "days": out_days,
    }
