"""调整链路的天气窗口（M3-3 · S1）：纯函数，不读配置、不查网络、不写日志、不改任何状态。

输入是 Java 侧交给 `/plan/adjust` 的**计划可编辑视图**（`days[]` 每天带 `date` 与 `city`），
输出是「这次该查哪些（日期, 城市）」以及一个可以并发的全局区间。

## 为什么不能复用 M3-2 的 `can_inject`

`can_inject` 服务于**生成**：那时还没有计划，只有槽位，所以它要求 `startDate` 落在
`[今天, 今天+窗口-1]` 之内。调整链路的时间点根本不同——行程往往**已经开始**
（`CONFIRMED` 之后进入 `IN_PROGRESS`），`startDate` 必然早于今天。照搬那条判定，
「今天下午下雨要不要把爬山改成室内」这个最典型的请求会被 100% 判成
`DATE_OUT_OF_RANGE`，一次都不查，功能等于没做。

所以这里改成**交集**：`行程日期区间 ∩ 预报窗口`。交集之外的日期（已过去、超出 10 天）
一律不查，与 PM 批复 D-A2 的五条一致。

## 三条自己加的约束（都来自"宁可少给，不可给错"）

1. **缺失日期只能按 `startDate + dayIndex - 1` 推算，绝不拿今天填。**
   填今天的后果是：一份 12 月底的行程会拿到 11 月今天那份天气，而模型完全看不出这是错的。
   `startDate` 也缺失时，这一天就是"无法定位"，直接排除——少一天参考，好过多一天假数据。
2. **城市按"交集内天数"排序后截断。** 5 城行程截到 3 城时，按出现顺序切会把主城市
   切掉，而主城市恰恰是待得最久的那个。
3. **`pairs` 是白名单。** 查询刻意用全局区间（这样能复用 M3-2 那套已经修对的超时/取消语义，
   不必在调整链路里重写一遍），但交给模型之前必须按 `(日期, 城市)` 过滤——
   否则多查的那几天会被模型当成"计划里本来就有的日期"。

## 这里为什么不做的事

- 不判断"这次调整是否与天气有关"：ops 是模型算出来的，注入时结构上不知道要改什么。
- 不判断计划状态：Java 侧只放行 `CONFIRMED` / `IN_PROGRESS`，这里再判一遍等于把同一条
  规则放在两个地方，改一处就会漏一处。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from app.tools.weather import FORECAST_WINDOW_DAYS
from app.tools.weather_brief import (
    MAX_INJECT_CITIES,
    SKIP_BAD_START_DATE,
    SKIP_NO_CITY,
    SKIP_NO_START_DATE,
    SKIP_OUT_OF_RANGE,
)


@dataclass(frozen=True)
class AdjustWindow:
    """一次调整的天气查询范围。

    `pairs` 用 `(ISO 日期, 城市)` 而不是 `date` 对象：它们最终要跟 `BriefDay.date`
    （已经是字符串）做白名单比较，早一步统一成字符串，下游就少一次格式转换的机会出错。
    `start` / `days` 是**全局区间**，仅供并发查询使用；它们可能比 `pairs` 覆盖的日期更宽
    （计划中间有无法定位的天时），所以下游渲染必须按 `pairs` 过滤。
    """

    allowed: bool
    reason: str | None
    pairs: tuple[tuple[str, str], ...] = ()
    cities: tuple[str, ...] = ()
    start: date | None = None
    days: int = 0

    @property
    def city_count(self) -> int:
        return len(self.cities)


def plan_weather_pairs(
    plan_view: Any,
    *,
    today: date,
    window_days: int = FORECAST_WINDOW_DAYS,
    max_cities: int = MAX_INJECT_CITIES,
) -> AdjustWindow:
    """算出这次调整该查哪些（日期, 城市）。

    返回 `allowed=False` 时 `reason` 说明原因，语义与 M3-1/M3-2 的枚举一致
    （`NO_CITY` / `NO_START_DATE` / `BAD_START_DATE` / `DATE_OUT_OF_RANGE`），
    刻意不新造一套说法。**任何情况都不抛异常**：调用方是生成建议的主链路，
    一个纯辅助判断不该有能力把它弄挂。
    """
    days = _days_of(plan_view)
    if not days:
        # 没有天就既没有城市也没有日期；「没有城市」是更可操作的那个提示
        return _no(SKIP_NO_CITY)

    start, bad_start = _plan_start(plan_view)

    located: list[tuple[date, str]] = []
    for day in days:
        city = str(day.get("city") or "").strip()
        if not city:
            continue
        when = _day_date(day, start)
        if when is None:
            continue
        located.append((when, city))

    if not located:
        # 三种"没法定位"对用户是三个不同动作：补城市 / 补日期 / 改日期格式
        if not _has_any_city(days):
            return _no(SKIP_NO_CITY)
        return _no(SKIP_BAD_START_DATE if bad_start else SKIP_NO_START_DATE)

    # 去重但保序：同一天同一城市出现两次时以后者无用，前者已经代表这一天
    unique: dict[tuple[date, str], None] = {}
    for when, city in located:
        unique.setdefault((when, city), None)

    window_days = max(int(window_days), 1)
    win_lo = today
    win_hi = today + timedelta(days=window_days - 1)
    # `win_lo = today` 同时承担"过去日期不查询"：交集天然把已过去的天排除在外
    in_window = [(when, city) for when, city in unique if win_lo <= when <= win_hi]
    if not in_window:
        return _no(SKIP_OUT_OF_RANGE)

    cities = _rank_cities(in_window, max_cities)
    rank = {city: index for index, city in enumerate(cities)}
    pairs = sorted(
        ((when, city) for when, city in in_window if city in rank),
        key=lambda item: (rank[item[1]], item[0]),
    )

    lo = min(when for when, _ in pairs)
    hi = max(when for when, _ in pairs)
    return AdjustWindow(
        allowed=True,
        reason=None,
        pairs=tuple((when.isoformat(), city) for when, city in pairs),
        cities=cities,
        start=lo,
        days=(hi - lo).days + 1,
    )


# ---------------------------------------------------------------- 内部


def _no(reason: str) -> AdjustWindow:
    return AdjustWindow(allowed=False, reason=reason)


def _days_of(plan_view: Any) -> list[Mapping[str, Any]]:
    if not isinstance(plan_view, Mapping):
        return []
    raw = plan_view.get("days")
    if not isinstance(raw, list):
        return []
    return [day for day in raw if isinstance(day, Mapping)]


def _plan_start(plan_view: Any) -> tuple[date | None, bool]:
    """计划级的 `startDate`，以及它是否"有值但解析不了"。"""
    raw = str(plan_view.get("startDate") or "").strip() if isinstance(plan_view, Mapping) else ""
    if not raw:
        return None, False
    try:
        return date.fromisoformat(raw), False
    except (TypeError, ValueError):
        return None, True


def _has_any_city(days: list[Mapping[str, Any]]) -> bool:
    return any(str(day.get("city") or "").strip() for day in days)


def _day_date(day: Mapping[str, Any], start: date | None) -> date | None:
    """这一天的日期：`date` 优先，坏了或缺失时按 `startDate + dayIndex - 1` 推算。

    为什么允许推算：`day.date` 在库里是可空列，而 `startDate` 是计划级字段。
    推算规则与生成时写入的完全一致，所以推出来的值与"本该有的值"是同一个。
    反过来，两者都拿不到就返回 `None`——**不兜底成今天**。
    """
    raw = str(day.get("date") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except (TypeError, ValueError):
            # 格式坏是数据问题，退到推算；不该因为一个坏字段否掉整次注入
            pass

    index = day.get("dayIndex")
    if start is not None and isinstance(index, int) and not isinstance(index, bool) and index >= 1:
        return start + timedelta(days=index - 1)
    return None


def _rank_cities(in_window: list[tuple[date, str]], max_cities: int) -> tuple[str, ...]:
    """按"交集内天数"降序、同数按首次出现顺序，截断到上限。

    天数相同为什么按首次出现：那是行程的自然顺序（先到的城市先出现），
    比按拼音或字典序更符合"哪座是主城市"的直觉。
    """
    count: dict[str, int] = {}
    first: dict[str, int] = {}
    for index, (_, city) in enumerate(in_window):
        count[city] = count.get(city, 0) + 1
        first.setdefault(city, index)
    ranked = sorted(count, key=lambda city: (-count[city], first[city]))
    return tuple(ranked[: max(int(max_cities), 1)])
