"""M3-2 · S1：注入摘要的纯函数单测（不起服务、不发网络请求）。

盯的是三件最容易出错、且出错后**看不出来**的事：

1. **能不能注入的边界**。PM 批复 D2 列了四条排除项，其中"没日期"和"超窗口"
   是最常被"顺手兜底"成今天的两个，兜底之后界面一切正常，只是天气是编的。
2. **只挑 verified=true 的字段**。未核实时 `display` 是「暂无数据」，
   抄进表里模型很可能当成真实值复述——这条必须靠断言守住，靠 review 守不住。
3. **覆盖范围要如实说**。部分城市/部分日期成功时，表里必须写明"哪些没有"，
   否则模型会默认表里就是全部，把 3 天行程排成 10 天。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.tools import weather_brief as wb

TODAY = date(2026, 11, 24)


def field(display: str, verified: bool = True) -> dict:
    """造一个与 M3-1 `build_day` 同形的 FactField。"""
    return {
        "value": 1 if verified else None,
        "display": display,
        "source": "TOOL" if verified else "NONE",
        "verified": verified,
        "fetchedAt": "2026-11-24T09:00:00+08:00" if verified else None,
        "expiresAt": "2026-11-24T10:00:00+08:00" if verified else None,
        "note": "",
    }


def day(date_str: str, *, weather="多云", temp="11–16℃", rain="降水概率 30%", wind="2级风",
        verified=True) -> dict:
    return {
        "dayIndex": 1,
        "date": date_str,
        "city": "成都",
        "resolvedCity": "成都",
        "weather": field(weather, verified),
        "temperature": field(temp, verified),
        "precipitation": field(rain, verified),
        "wind": field(wind, verified),
    }


def payload(days: list[dict], *, city="成都", ok=True) -> dict:
    return {
        "ok": ok,
        "source": "QWEATHER",
        "attribution": "天气服务由和风天气驱动",
        "city": city,
        "resolvedCity": city,
        "fetchedAt": "2026-11-24T09:00:00+08:00",
        "expiresAt": "2026-11-24T10:00:00+08:00",
        "days": days,
    }


SLOTS_OK = {"destination": "成都", "destinations": ["成都"], "startDate": "2026-11-24", "days": 3}


# ================================================================ can_inject


def test_can_inject_when_everything_present():
    d = wb.can_inject(SLOTS_OK, today=TODAY)
    assert d.allowed is True
    assert d.reason is None
    assert d.cities == ("成都",)
    assert d.days == 3
    assert d.start == date(2026, 11, 24)


def test_no_city_means_no_injection():
    d = wb.can_inject({"startDate": "2026-11-24", "days": 3}, today=TODAY)
    assert (d.allowed, d.reason) == (False, wb.SKIP_NO_CITY)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_start_date_never_falls_back_to_today(value):
    """没有出发日期就不查。兜底成"今天"正是 09 §4.3 被否掉的 B 方案。"""
    d = wb.can_inject({**SLOTS_OK, "startDate": value}, today=TODAY)
    assert (d.allowed, d.reason) == (False, wb.SKIP_NO_START_DATE)


def test_unparsable_start_date_is_rejected_not_guessed():
    d = wb.can_inject({**SLOTS_OK, "startDate": "下个月"}, today=TODAY)
    assert (d.allowed, d.reason) == (False, wb.SKIP_BAD_START_DATE)


def test_start_date_yesterday_is_out_of_range():
    d = wb.can_inject({**SLOTS_OK, "startDate": "2026-11-23"}, today=TODAY)
    assert (d.allowed, d.reason) == (False, wb.SKIP_OUT_OF_RANGE)


def test_start_date_today_is_allowed():
    d = wb.can_inject({**SLOTS_OK, "startDate": TODAY.isoformat()}, today=TODAY)
    assert d.allowed is True


def test_last_day_of_window_is_allowed():
    """窗口是 [今天, 今天+9]；第 10 天（今天+9）必须还能查。"""
    d = wb.can_inject({**SLOTS_OK, "startDate": "2026-12-03"}, today=TODAY)
    assert d.allowed is True
    assert d.days == 1, "第 10 天只剩 1 天窗口，天数要被窗口截断"


def test_one_day_past_window_is_rejected():
    d = wb.can_inject({**SLOTS_OK, "startDate": "2026-12-04"}, today=TODAY)
    assert (d.allowed, d.reason) == (False, wb.SKIP_OUT_OF_RANGE)


def test_days_clamped_to_remaining_window():
    """11-30 出发、行程 7 天，但窗口只剩 4 天 → 只查 4 天。"""
    d = wb.can_inject({**SLOTS_OK, "startDate": "2026-11-30", "days": 7}, today=TODAY)
    assert d.allowed is True
    assert d.days == 4


def test_unknown_days_fills_the_remaining_window():
    """没说天数就用满剩余窗口：不知道行程多长时，只有填满才有用。"""
    d = wb.can_inject({**SLOTS_OK, "days": None}, today=TODAY)
    assert d.days == 10


def test_too_many_days_does_not_exceed_window():
    d = wb.can_inject({**SLOTS_OK, "days": 30}, today=TODAY)
    assert d.days == 10


def test_cities_are_deduped_and_capped_in_order():
    slots = {
        **SLOTS_OK,
        "destinations": ["成都", "都江堰", "成都", "乐山", "峨眉山"],
    }
    d = wb.can_inject(slots, today=TODAY)
    assert d.cities == ("成都", "都江堰", "乐山"), "去重保序，且截断到 3 个——截断要在查之前做"


def test_single_destination_falls_back_when_list_missing():
    d = wb.can_inject({"destination": "成都", "startDate": "2026-11-24", "days": 2}, today=TODAY)
    assert d.cities == ("成都",)


# ---------------------------------------------------------------- 查询范围：PM 四条规则
#
# PM 原话：① 有 days 按行程天数；② 有 endDate 查询范围不得超过 endDate；
# ③ 都没有则从 startDate 查满剩余窗口；④ 无论如何不超过 10 天。
# ②是"上限"而不是"替代"：两者同时存在时取更小的那个 ——
# 少查一天只是少一条参考，多查一天则是把行程外的天气塞进 prompt。


def test_end_date_caps_the_range_below_days():
    """出发 11-24、行程 7 天、但 11-26 就结束 → 只能查 3 天。"""
    slots = {**SLOTS_OK, "days": 7, "endDate": "2026-11-26"}
    d = wb.can_inject(slots, today=TODAY)
    assert d.allowed is True
    assert d.days == 3


def test_end_date_used_when_days_missing():
    """只有日期区间、没有天数：按区间查，不要退化成"填满 10 天窗口"。"""
    slots = {**SLOTS_OK, "days": None, "endDate": "2026-11-26"}
    d = wb.can_inject(slots, today=TODAY)
    assert d.days == 3


def test_end_date_beyond_window_is_clamped_by_window():
    """endDate 再远也不能越过 10 天窗口（规则④压住规则②）。"""
    slots = {**SLOTS_OK, "days": None, "endDate": "2026-12-31"}
    d = wb.can_inject(slots, today=TODAY)
    assert d.days == 10


def test_days_wins_when_end_date_is_later():
    """行程天数比 endDate 短时按天数查：多查的日子没有任何用途。"""
    slots = {**SLOTS_OK, "days": 2, "endDate": "2026-11-29"}
    d = wb.can_inject(slots, today=TODAY)
    assert d.days == 2


@pytest.mark.parametrize("value", ["2026-11-20", "下个月", ""])
def test_unusable_end_date_is_ignored_not_fatal(value):
    """早于出发日 / 格式错 / 为空的 endDate 一律忽略。

    这里刻意**不**判成"不能注入"：槽位抽取出问题，不该连带把整次注入否掉。
    早于出发日的输入如果拿去当上限，会算出零天甚至负数天。
    """
    slots = {**SLOTS_OK, "days": 3, "endDate": value}
    d = wb.can_inject(slots, today=TODAY)
    assert d.allowed is True
    assert d.days == 3


def test_max_cities_is_configurable():
    """城市上限来自配置（`WEATHER_INJECT_MAX_CITIES`），不是写死在函数里。"""
    slots = {**SLOTS_OK, "destinations": ["成都", "都江堰", "乐山"]}
    assert wb.can_inject(slots, today=TODAY, max_cities=1).cities == ("成都",)
    assert wb.can_inject(slots, today=TODAY, max_cities=0).cities == ("成都",), "下限兜底为 1"


# ================================================================ build_brief


def test_brief_keeps_only_verified_fields():
    """未核实的字段必须留空，不能把「暂无数据」抄进表里。"""
    row = day(
        "2026-11-24",
        weather="多云",
        temp="暂无数据",
        rain="暂无数据",
        wind="暂无数据",
    )
    row["temperature"] = field("暂无数据", verified=False)
    row["precipitation"] = field("暂无数据", verified=False)
    row["wind"] = field("暂无数据", verified=False)
    brief = wb.build_brief([("成都", payload([row]))])

    assert brief is not None
    only = brief.days[0]
    assert only.weather_text == "多云"
    assert only.temp_range is None and only.precip_prob is None and only.wind is None


def test_brief_returns_none_when_nothing_is_verified():
    """全部未核实 = 没数据。必须返回 None，而不是一张只有表头的空表。"""
    brief = wb.build_brief([("成都", payload([day("2026-11-24", verified=False)]))])
    assert brief is None


def test_brief_returns_none_when_all_cities_failed():
    brief = wb.build_brief([
        ("成都", {"ok": False, "reason": "CIRCUIT_OPEN"}),
        ("乐山", None),
    ])
    assert brief is None


def test_day_without_weather_text_is_dropped():
    """锚点是天气文字：它没核实，这行就没有可做决策的信息。"""
    blind = day("2026-11-25")
    blind["weather"] = field("暂无数据", verified=False)
    brief = wb.build_brief([("成都", payload([day("2026-11-24"), blind]))])
    assert brief is not None
    assert [r.date for r in brief.days] == ["2026-11-24"]


def test_partial_city_failure_lists_uncovered_cities():
    """部分城市成功：成功的进表，失败的必须写进 uncovered，不许当成有数据。"""
    brief = wb.build_brief([
        ("成都", payload([day("2026-11-24")], city="成都")),
        ("昆明", {"ok": False, "reason": "CITY_UNKNOWN"}),
    ])
    assert brief is not None
    assert brief.covered_cities == ("成都",)
    assert brief.uncovered_cities == ("昆明",)


def test_each_row_keeps_its_own_city():
    """A 城的天气绝不能挂到 B 城头上。"""
    cd = day("2026-11-24", weather="中雨")
    cd["city"] = "成都"
    km = day("2026-11-24", weather="晴")
    km["city"] = "昆明"
    brief = wb.build_brief([
        ("成都", payload([cd], city="成都")),
        ("昆明", payload([km], city="昆明")),
    ])
    assert brief is not None
    pairs = {(r.city, r.weather_text) for r in brief.days}
    assert pairs == {("成都", "中雨"), ("昆明", "晴")}


def test_rows_sorted_by_city_order_then_date():
    """同样的输入必须渲染出同样的文本，否则断言和复现都无从谈起。"""
    brief = wb.build_brief([
        ("成都", payload([
            day("2026-11-26", weather="阴"),
            day("2026-11-24", weather="多云"),
            day("2026-11-25", weather="中雨"),
        ])),
    ])
    assert brief is not None
    assert [r.date for r in brief.days] == ["2026-11-24", "2026-11-25", "2026-11-26"]


def test_empty_brief_is_not_constructible():
    """空摘要直接在构造期炸掉：让它根本没机会渲染成一张空表。"""
    with pytest.raises(ValueError):
        wb.WeatherBrief(
            source="QWEATHER", attribution="", fetched_at="", days=(),
            covered_cities=(), uncovered_cities=(),
        )


# ================================================================ 渲染


def test_render_states_coverage_before_the_table():
    brief = wb.build_brief([
        ("成都", payload([day("2026-11-24")], city="成都")),
        ("昆明", {"ok": False}),
    ])
    assert brief is not None
    text = wb.render_brief_block(brief)

    assert "覆盖城市：成都" in text
    assert "以下城市本次没有拿到预报" in text and "昆明" in text
    assert "覆盖日期：2026-11-24" in text
    assert "表中没有列出的日期没有预报" in text


def test_render_never_leaks_degraded_placeholder():
    """「暂无数据」是降级文案，出现在注入块里会被模型当成真实值。"""
    row = day("2026-11-24")
    row["wind"] = field("暂无数据", verified=False)
    brief = wb.build_brief([("成都", payload([row]))])
    assert brief is not None
    text = wb.render_brief_block(brief)

    assert "暂无数据" not in text
    assert "| — |" in text, "缺的列画破折号，不填 0 也不填 N/A"


def test_render_forbids_copying_and_inventing():
    brief = wb.build_brief([("成都", payload([day("2026-11-24")]))])
    assert brief is not None
    text = wb.render_brief_block(brief)

    for must in ("不得把表中的温度、降水概率、风级等数值复制进计划",
                 "不得修改数值后重新表述",
                 "不得把这些数据说成是用户提供的信息",
                 "不得自行编造"):
        assert must in text, f"注入块缺了约束：{must}"


def test_render_includes_source_and_time():
    brief = wb.build_brief([("成都", payload([day("2026-11-24")]))])
    assert brief is not None
    text = wb.render_brief_block(brief)
    assert "和风天气" in text
    assert "2026-11-24T09:00:00+08:00" in text


def test_render_table_has_one_row_per_day():
    brief = wb.build_brief([("成都", payload([
        day("2026-11-24", weather="多云"),
        day("2026-11-25", weather="中雨"),
    ]))])
    assert brief is not None
    text = wb.render_brief_block(brief)
    assert text.count("| 2026-11-24 | 成都 |") == 1
    assert text.count("| 2026-11-25 | 成都 |") == 1


# ================================================================ aiNote


def test_ai_note_uses_real_metadata():
    """这句话必须由程序用真实元数据拼，不能让模型自己写。"""
    brief = wb.build_brief([
        ("成都", payload([day("2026-11-24"), day("2026-11-25"), day("2026-11-26")], city="成都")),
        ("昆明", {"ok": False}),
    ])
    assert brief is not None
    note = wb.brief_ai_note(brief)

    assert "成都" in note
    assert "2026-11-24T09:00:00+08:00" in note
    assert "2026-11-24 至 11-26" in note
    assert "昆明未取得预报，未参考" in note


def test_ai_note_lists_non_contiguous_dates_instead_of_pretending_a_span():
    brief = wb.build_brief([("成都", payload([
        day("2026-11-24"), day("2026-11-26"),
    ]))])
    assert brief is not None
    note = wb.brief_ai_note(brief)
    assert "至" not in note, "不连续就别用'至'，否则像是在说连续覆盖"
    assert "2026-11-24、2026-11-26" in note


def test_ai_note_without_uncovered_cities_has_no_extra_clause():
    brief = wb.build_brief([("成都", payload([day("2026-11-24")]))])
    assert brief is not None
    note = wb.brief_ai_note(brief)
    assert "未取得预报" not in note
    assert note.endswith("。")
