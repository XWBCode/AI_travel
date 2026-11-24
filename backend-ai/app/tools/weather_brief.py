"""把天气结果压成一张"可以塞进 prompt"的表（M3-2 · S1）。

本模块**只做纯函数**：判断能不能注入、把已拿到的天气解析结果压成不可变摘要、渲染成文本。
不发起任何网络请求、不读配置、不写日志，因此可以只用单测把它审完。

## 为什么单独一个模块，而不是塞进 weather.py

`weather.py` 是 M3-1 已验收的编排层（缓存/熔断/降级枚举都在那条线上），
PM 明确要求「不要为了 M3-2 顺手重构已验收的天气链路」。所以这里**只消费**
`fetch_forecast()` 的返回，不改变它；接线在 S2 做，且带开关。

## 三条硬约束（来自 PM 对 M3-2 设计的批复）

1. **天气只能作为辅助上下文**：模型可以据此调整活动/衣物/室内外安排，
   但不得把温度、降水概率、风级等数值复制进计划，不得改写后转述，
   不得伪装成用户提供的信息，不得在没数据时编造。
2. **只挑 `verified=true` 的字段**：M3-1 的 FactField 里 `verified=false` 时
   `display` 也是「暂无数据」。把它抄进表里，模型很可能当成真实值复述——
   那是"看起来最像真数据的假信息"，正是本项目红线。
3. **多城必须各自绑定城市、且如实说明覆盖范围**：哪几个城市有数据、哪几个没有，
   都要写进表里。不许把"A 城天气"用在 B 城，也不许把部分成功说成全部齐全。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from app.tools.weather import FORECAST_WINDOW_DAYS

# 注入时最多查/写几个城市。上限本身是额度与延迟的折中（PM 批复 D4：并发上限 3）
MAX_INJECT_CITIES = 3

# 缺失单元格的占位。用破折号而不是 "N/A" 或 0：
# 破折号在中文语境里就是"没有这一项"，而 0 会被读成"降水概率 0%"——两者天差地别。
_EMPTY = "—"

# 注入块里"怎么用"的约束。这段是防 R4（模型改写数值形成新的编造来源）的唯一手段，
# 改它等于改安全边界，必须同步改 tests/test_weather_brief.py 里的对应断言。
_USAGE_RULES = """你可以据此调整每日安排（例如把户外项放到天气更好的那天、雨天优先室内、按气温提示衣物），
但必须遵守：
- 不得把表中的温度、降水概率、风级等数值复制进计划；
- 不得修改数值后重新表述；
- 不得把这些数据说成是用户提供的信息；
- 表中没有列出的日期或城市，代表没有预报，不得自行编造。"""

# 跳过原因。刻意复用 M3-1 的 reason 语义，避免系统里出现第二套说法
SKIP_DISABLED = "PROVIDER_DISABLED"
SKIP_NO_CITY = "NO_CITY"
SKIP_NO_START_DATE = "NO_START_DATE"
SKIP_BAD_START_DATE = "BAD_START_DATE"
SKIP_OUT_OF_RANGE = "DATE_OUT_OF_RANGE"
SKIP_NO_DATA = "UPSTREAM_ERROR"


@dataclass(frozen=True)
class InjectDecision:
    """能不能注入、为什么不能、如果能要查哪些城市多少天。

    `cities` 只放**去重后**的城市名，且已按 `MAX_INJECT_CITIES` 截断——
    截断要在这里做而不是在渲染时做，否则"查了 5 个只写 3 个"会浪费额度还留下不一致。
    """

    allowed: bool
    reason: str | None
    cities: tuple[str, ...] = ()
    days: int = 0
    start: date | None = None


@dataclass(frozen=True)
class BriefDay:
    """表里的一行。每个字段要么是已核实的 display，要么是 None（渲染成破折号）。"""

    date: str
    city: str
    weather_text: str | None
    temp_range: str | None
    precip_prob: str | None
    wind: str | None


@dataclass(frozen=True)
class WeatherBrief:
    """注入摘要。构造完即不可变，下游没有机会往里塞未核实字段。"""

    source: str
    attribution: str
    fetched_at: str
    days: tuple[BriefDay, ...]
    covered_cities: tuple[str, ...]
    uncovered_cities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("WeatherBrief 不允许为空：没数据就应该返回 None，而不是一个空壳")


# ---------------------------------------------------------------- 能不能注入


def can_inject(
    slots: Mapping[str, Any],
    *,
    today: date,
    window_days: int = FORECAST_WINDOW_DAYS,
) -> InjectDecision:
    """按 PM 批复 D2 的四条排除项判断：没日期、超窗口、没城市、城市不可用。

    「城市无法解析或没有有效坐标」在这里不做判断——那要看坐标表，
    属于 `resolve_city` 的职责，由实际查询时的 `CITY_UNKNOWN` 反映，
    其结果会体现为"该城没有数据"（见 `build_brief` 的 uncovered_cities）。
    放在这里判会让纯函数依赖坐标表，测试也不再是纯的。
    """
    destinations = _destinations(slots)
    if not destinations:
        return InjectDecision(False, SKIP_NO_CITY)

    # 先 strip 再判空：纯空白串是真值，直接送进 fromisoformat 会被判成"格式错误"，
    # 而它其实是"没说日期"——两者对用户的含义完全不同（一个要补，一个是代码 bug）
    raw_start = str(slots.get("startDate") or "").strip()
    if not raw_start:
        # 刻意不兜底成"今天"：没有出发日期就没有"哪天的天气"，兜底等于编造（09 §4.3 方案 B 已被否）
        return InjectDecision(False, SKIP_NO_START_DATE)

    try:
        start = date.fromisoformat(raw_start)
    except (TypeError, ValueError):
        return InjectDecision(False, SKIP_BAD_START_DATE)

    window_end = today + timedelta(days=window_days - 1)
    if start < today or start > window_end:
        # 已过去的日期同样归到这一类：用户改日期就行，与"太远"是同一个动作
        return InjectDecision(False, SKIP_OUT_OF_RANGE)

    remaining = (window_end - start).days + 1
    wanted = slots.get("days")
    try:
        wanted_days = int(wanted) if wanted is not None else 0
    except (TypeError, ValueError):
        wanted_days = 0
    # 没说天数就用满剩余窗口：不知道行程多长时，只有把窗口填满才有用；
    # 上限仍受窗口约束，所以最多也就 10 天，不会失控
    days = min(wanted_days, remaining) if wanted_days > 0 else remaining
    days = max(days, 1)

    return InjectDecision(True, None, tuple(destinations[:MAX_INJECT_CITIES]), days, start)


def _destinations(slots: Mapping[str, Any]) -> list[str]:
    """取城市列表：`destinations` 优先，退到 `destination`；去重且保序。"""
    raw: list[Any] = []
    multi = slots.get("destinations")
    if isinstance(multi, (list, tuple)):
        raw = [c for c in multi]
    if not raw and slots.get("destination"):
        raw = [slots.get("destination")]

    seen: dict[str, None] = {}
    for item in raw:
        name = str(item or "").strip()
        if name and name not in seen:
            seen[name] = None
    return list(seen)


# ---------------------------------------------------------------- 压成摘要


def build_brief(
    forecasts: Sequence[tuple[str, Mapping[str, Any] | None]],
) -> WeatherBrief | None:
    """把**已经拿到的**查询结果压成摘要；一个可用字段都没有就返回 None。

    参数是 `(计划里的城市写法, fetch_forecast 的返回)` 序列。
    返回 `None` 与"返回一个空摘要"必须区分：前者代表"这次不注入"，
    后者会让渲染出一张只有表头的空表，模型很可能自行补内容。
    """
    covered: list[str] = []
    uncovered: list[str] = []
    rows: list[BriefDay] = []
    source = ""
    attribution = ""
    fetched_at = ""

    for city, payload in forecasts:
        if not payload or not payload.get("ok"):
            uncovered.append(city)
            continue
        city_rows = [_brief_day(day, city) for day in payload.get("days") or []]
        city_rows = [row for row in city_rows if row is not None]
        if not city_rows:
            uncovered.append(city)
            continue
        covered.append(city)
        rows.extend(city_rows)
        # 多城时元数据取第一份成功的：同一次注入只查一个 provider，
        # source/attribution 必然相同，取谁都一样，没必要为此引入"取最晚"这类规则
        source = source or str(payload.get("source") or "")
        attribution = attribution or str(payload.get("attribution") or "")
        fetched_at = fetched_at or str(payload.get("fetchedAt") or "")

    if not rows:
        return None
    # 按城市顺序、再按日期排，保证同样的输入永远渲染出同样的文本（便于断言与复现）
    order = {city: idx for idx, city in enumerate(covered)}
    rows.sort(key=lambda r: (order.get(r.city, 0), r.date))
    return WeatherBrief(
        source=source,
        attribution=attribution,
        fetched_at=fetched_at,
        days=tuple(rows),
        covered_cities=tuple(covered),
        uncovered_cities=tuple(uncovered),
    )


def _brief_day(day: Mapping[str, Any], city: str) -> BriefDay | None:
    """一天 → 一行。**锚点是天气文字**：它都没核实，这天就没有可注入的东西。

    为什么不以温度为准：天气文字是用户唯一能直接拿来做安排决策的字段
    （"中雨"能推出"别爬山"，"11–16℃"推不出来）。
    """
    weather_text = _verified(day.get("weather"))
    if weather_text is None:
        return None
    return BriefDay(
        date=str(day.get("date") or ""),
        city=city,
        weather_text=weather_text,
        temp_range=_verified(day.get("temperature")),
        precip_prob=_verified(day.get("precipitation")),
        wind=_verified(day.get("wind")),
    )


def _verified(field: Any) -> str | None:
    """取一个 FactField 的 display，**只在 verified=true 时**。

    未核实时的 display 是「暂无数据」，抄进表里会被模型当成真实值复述；
    所以这里返回 None，由渲染层画破折号。
    """
    if not isinstance(field, Mapping):
        return None
    if field.get("verified") is not True:
        return None
    display = field.get("display")
    if not isinstance(display, str) or not display.strip():
        return None
    return display.strip()


# ---------------------------------------------------------------- 渲染


def render_brief_block(brief: WeatherBrief) -> str:
    """渲染成可以直接放进 user 消息的一段文本。

    覆盖范围必须写在全量数据前面：模型读表格时默认假设"表里就是全部"，
    所以要先说清"哪些城市/日期是**没有**预报的"，否则它会把 3 天行程排成 10 天。
    """
    lines: list[str] = ["【已核实的天气预报】"]

    meta = [f"数据来源：{brief.attribution or brief.source or '第三方天气服务'}"]
    if brief.fetched_at:
        meta.append(f"获取时间：{brief.fetched_at}")
    lines.append("，".join(meta) + "。")
    lines.append(
        f"覆盖城市：{'、'.join(brief.covered_cities)}。"
        + (f"以下城市本次没有拿到预报，请勿为它们编造天气：{'、'.join(brief.uncovered_cities)}。"
           if brief.uncovered_cities else "")
    )
    lines.append(f"覆盖日期：{'、'.join(_unique_dates(brief))}。表中没有列出的日期没有预报。")
    lines.append("")
    lines.append("| 日期 | 城市 | 天气 | 气温 | 降水概率 | 风 |")
    lines.append("|---|---|---|---|---|---|")
    for row in brief.days:
        lines.append(
            f"| {row.date} | {row.city} | {row.weather_text or _EMPTY} | "
            f"{row.temp_range or _EMPTY} | {row.precip_prob or _EMPTY} | {row.wind or _EMPTY} |"
        )
    lines.append("")
    lines.append(_USAGE_RULES)
    return "\n".join(lines)


def _unique_dates(brief: WeatherBrief) -> list[str]:
    seen: dict[str, None] = {}
    for row in brief.days:
        if row.date and row.date not in seen:
            seen[row.date] = None
    return list(seen)


def brief_ai_note(brief: WeatherBrief, *, plan_days: Iterable[int] = ()) -> str:
    """给计划 `aiNote` 追加的一句说明。

    **必须由程序用真实元数据拼**，不能让模型自己写——模型的数字随时可能是编的，
    而这句话的全部意义就是"我们确实用了哪一份数据"（PM 批复：不得让模型自行编造）。

    只陈述事实：来源、覆盖城市、获取时间、覆盖日期。不评价天气好坏。
    """
    parts = [f"本计划生成时参考了{'、'.join(brief.covered_cities)}的天气预报"]
    if brief.fetched_at:
        parts.append(f"（获取时间 {brief.fetched_at}")
        parts.append(f"，覆盖 {_span(_unique_dates(brief))}）" if _unique_dates(brief) else "）")
    else:
        parts.append(f"（覆盖 {_span(_unique_dates(brief))}）" if _unique_dates(brief) else "")
    if brief.uncovered_cities:
        parts.append(f"；{'、'.join(brief.uncovered_cities)}未取得预报，未参考")
    return "".join(parts) + "。"


def _span(dates: list[str]) -> str:
    """`['2026-11-24', '2026-11-25', '2026-11-26']` → `2026-11-24 至 11-26`。

    连续区间用"至"更短也更好读；不连续时原样罗列——宁可啰嗦也不能让人误以为连续。
    """
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0]
    first, last = dates[0], dates[-1]
    try:
        span = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    except ValueError:
        return "、".join(dates)
    if span != len(dates):
        return "、".join(dates)
    return f"{first} 至 {last[5:]}"
