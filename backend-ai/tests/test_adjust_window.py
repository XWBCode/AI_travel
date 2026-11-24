"""M3-3 · S1：调整链路天气窗口的纯函数单测（不起服务、不读配置、不发网络请求）。

盯的是四件"错了也看不出来"的事：

1. **日期怎么来**。`day.date` 缺失时按 `startDate + dayIndex - 1` 推算，两者都没有就排除。
   最危险的写法是"兜底成今天"——界面一切正常，只是天气是另一天的。
2. **交集，而不是包含**。调整的行程常常已经开始，若沿用 M3-2 那条"startDate 必须落在窗口内"
   的判定，最典型的请求会被 100% 挡掉；反过来，若不做交集，"全部已过去"的行程会去查一批
   没有意义的历史日期。
3. **边界**。窗口最后一天（`今天+9`）要算在里，第 11 天要排除。
4. **城市白名单**。截断不能按出现顺序切——主城市往往不是第一个；`pairs` 要能当白名单用，
   因为它决定了"多查的那几天"会不会被交给模型。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.tools import adjust_window as aw

TODAY = date(2026, 11, 24)
# 窗口 = 2026-11-24 ~ 2026-12-03（10 天）
LAST_DAY = date(2026, 12, 3)


def plan(start=None, days=None) -> dict:
    return {"title": "成都四日", "totalDays": len(days or []), "startDate": start, "status": "IN_PROGRESS",
            "days": list(days or [])}


def day(index: int, date_str=None, city="成都", **extra) -> dict:
    node = {"dayIndex": index, "date": date_str, "city": city, "theme": None, "items": []}
    node.update(extra)
    return node


def iso(value: date) -> str:
    return value.isoformat()


# ---------------------------------------------------------------- 日期来源


def test_all_dates_present():
    view = plan(None, [day(1, "2026-11-24"), day(2, "2026-11-25")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is True
    assert result.reason is None
    assert result.pairs == (("2026-11-24", "成都"), ("2026-11-25", "成都"))
    assert result.cities == ("成都",)
    assert result.start == date(2026, 11, 24)
    assert result.days == 2


def test_missing_day_date_falls_back_to_start_plus_index():
    """`day.date` 是库里的可空列，`startDate` 是计划级字段：缺前者时按后者推算。"""
    view = plan("2026-11-24", [day(1), day(2), day(3, city="昆明")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is True
    assert result.pairs == (
        ("2026-11-24", "成都"),
        ("2026-11-25", "成都"),
        ("2026-11-26", "昆明"),
    )
    assert result.days == 3


def test_no_start_and_no_day_date_is_not_injected():
    """两个日期来源都没有 —— 必须放弃，**不许**用今天顶上。"""
    view = plan(None, [day(1), day(2)])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is False
    assert result.reason == aw.SKIP_NO_START_DATE
    assert result.pairs == ()
    assert result.start is None


def test_bad_start_date_still_works_when_day_dates_exist():
    """`startDate` 格式坏是槽位/数据问题，不该连带把整次注入否掉。"""
    view = plan("2026/11/24", [day(1, "2026-11-24"), day(2, "2026-11-25")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is True
    assert len(result.pairs) == 2


def test_bad_start_date_reason_when_nothing_can_be_located():
    view = plan("2026/11/24", [day(1), day(2)])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is False
    # 「格式错」与「没说日期」对用户是两个动作：改格式 vs 补日期
    assert result.reason == aw.SKIP_BAD_START_DATE


def test_blank_start_date_is_treated_as_missing():
    """纯空白串是真值，直接送进 fromisoformat 会被判成"格式错"——它其实是"没说"。"""
    view = plan("   ", [day(1)])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.reason == aw.SKIP_NO_START_DATE


# ---------------------------------------------------------------- 城市


def test_no_city_anywhere():
    view = plan("2026-11-24", [day(1, "2026-11-24", city="  "), day(2, "2026-11-25", city=None)])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is False
    assert result.reason == aw.SKIP_NO_CITY


def test_empty_days_is_not_injected():
    result = aw.plan_weather_pairs(plan("2026-11-24", []), today=TODAY)
    assert result.allowed is False
    assert result.reason == aw.SKIP_NO_CITY


def test_non_mapping_plan_view_does_not_raise():
    """主链路调它，纯辅助判断绝不能有抛异常的能力。"""
    result = aw.plan_weather_pairs(None, today=TODAY)
    assert result.allowed is False
    assert result.reason == aw.SKIP_NO_CITY


def test_duplicate_date_city_pairs_collapse():
    view = plan(None, [day(1, "2026-11-24"), day(1, "2026-11-24"), day(2, "2026-11-25")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.pairs == (("2026-11-24", "成都"), ("2026-11-25", "成都"))


def test_city_truncation_keeps_the_busiest_not_the_first():
    """5 城行程截到 2 城时按出现顺序切会把主城市切掉。"""
    view = plan(None, [
        day(1, "2026-11-24", city="乐山"),
        day(2, "2026-11-25", city="成都"),
        day(3, "2026-11-26", city="成都"),
        day(4, "2026-11-27", city="成都"),
        day(5, "2026-11-28", city="昆明"),
    ])
    result = aw.plan_weather_pairs(view, today=TODAY, max_cities=2)

    assert result.cities == ("成都", "乐山")
    assert all(city != "昆明" for _, city in result.pairs)


def test_same_count_cities_keep_itinerary_order():
    view = plan(None, [
        day(1, "2026-11-24", city="昆明"),
        day(2, "2026-11-25", city="成都"),
    ])
    result = aw.plan_weather_pairs(view, today=TODAY, max_cities=1)

    assert result.cities == ("昆明",)


def test_max_cities_zero_still_keeps_one():
    """上限配成 0 是配置事故，返回"一个城市都不查"会让功能静默失效。"""
    view = plan(None, [day(1, "2026-11-24", city="成都")])
    result = aw.plan_weather_pairs(view, today=TODAY, max_cities=0)

    assert result.allowed is True
    assert result.cities == ("成都",)


# ---------------------------------------------------------------- 窗口与交集


def test_trip_already_started_queries_from_today():
    """这是 M3-3 存在的理由：`IN_PROGRESS` 的行程 startDate 必然早于今天。"""
    view = plan("2026-11-22", [day(1, "2026-11-22"), day(2, "2026-11-23"), day(3, "2026-11-24"),
                               day(4, "2026-11-25"), day(5, "2026-11-26")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is True
    # 过去的两天不查：拿不到预报，查到也没有意义
    assert result.pairs == (("2026-11-24", "成都"), ("2026-11-25", "成都"), ("2026-11-26", "成都"))
    assert result.start == TODAY
    assert result.days == 3


def test_all_dates_in_the_past_is_not_injected():
    view = plan("2026-11-20", [day(1, "2026-11-20"), day(2, "2026-11-21")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is False
    assert result.reason == aw.SKIP_OUT_OF_RANGE


def test_all_dates_beyond_window_is_not_injected():
    view = plan("2026-12-20", [day(1, "2026-12-20"), day(2, "2026-12-21")])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.allowed is False
    assert result.reason == aw.SKIP_OUT_OF_RANGE


def test_window_boundary_last_day_included_next_excluded():
    view = plan(None, [day(1, iso(LAST_DAY)), day(2, iso(LAST_DAY + timedelta(days=1)))])
    result = aw.plan_weather_pairs(view, today=TODAY)

    assert result.pairs == ((iso(LAST_DAY), "成都"),)
    assert result.days == 1


def test_query_span_never_exceeds_window():
    """15 天的行程：查询范围必须停在 10 天，这是数据源能力而不是业务选择。"""
    days = [day(i, iso(TODAY + timedelta(days=i - 1))) for i in range(1, 16)]
    result = aw.plan_weather_pairs(plan(str(TODAY), days), today=TODAY)

    assert len(result.pairs) == aw.FORECAST_WINDOW_DAYS
    assert result.days == aw.FORECAST_WINDOW_DAYS
    assert result.pairs[-1][0] == iso(LAST_DAY)


def test_start_and_days_cover_the_whole_whitelist():
    """区间是给并发查询用的，必须覆盖**所有**白名单日期，哪怕中间有跨城。"""
    view = plan(None, [day(1, "2026-11-25", city="昆明"), day(2, "2026-11-24", city="成都"),
                       day(3, "2026-11-28", city="乐山")])
    result = aw.plan_weather_pairs(view, today=TODAY, max_cities=3)

    assert result.start == date(2026, 11, 24)
    assert result.days == 5          # 24 号到 28 号，中间空档也要在区间内


# ---------------------------------------------------------------- 形态与确定性


def test_pairs_are_iso_strings_in_itinerary_order():
    view = plan(None, [day(1, "2026-11-25", city="成都"), day(2, "2026-11-24", city="昆明")])
    result = aw.plan_weather_pairs(view, today=TODAY, max_cities=2)

    assert all(isinstance(when, str) and isinstance(city, str) for when, city in result.pairs)
    # 先按城市排名、再按日期：成都排在昆明前是因为它先出现在行程里，
    # 尽管它的日期比昆明晚——排序键是 (城市, 日期)，不是纯粹按日期
    assert result.pairs == (("2026-11-25", "成都"), ("2026-11-24", "昆明"))


def test_same_input_same_output():
    view = plan("2026-11-22", [day(1), day(2, city="昆明"), day(3, "2026-11-26")])
    first = aw.plan_weather_pairs(view, today=TODAY)
    second = aw.plan_weather_pairs(view, today=TODAY)

    assert first == second


@pytest.mark.parametrize("window_days", [1, 3, 10])
def test_days_never_exceed_given_window(window_days):
    days = [day(i, iso(TODAY + timedelta(days=i - 1))) for i in range(1, 11)]
    result = aw.plan_weather_pairs(plan(str(TODAY), days), today=TODAY, window_days=window_days)

    assert result.allowed is True
    assert result.days <= window_days
    assert len(result.pairs) == window_days
