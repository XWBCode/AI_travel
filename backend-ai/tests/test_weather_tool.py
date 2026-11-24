"""天气工具层单测（W-1 / W-2 / W-3 / 窗口与坐标解析）。

全部离线：用 `httpx.MockTransport` 顶替真实上游。
这不是为了跑得快，而是因为**「上游挂了会怎样」必须能测**——
真调 Open-Meteo 永远测不出超时和 500，只能在事故里第一次见到。

三个重点：
1. 成功路径产出的 FactField 必须能过契约（`factFieldBase` 的 allOf 分支），
   否则前端拿到的"真值"在 Java 侧会被判非法；
2. 没有出发日期时**不许调上游**（PM 已把日期口径定为 C，兜底成"今天"就是 B，已被否）；
3. 超出预报窗口只降级，不用气候平均顶替。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from app.plan.contract import load_schema
from app.tools import weather, weather_circuit
from app.tools.weather import (
    R_CIRCUIT_OPEN,
    R_CITY_UNKNOWN,
    R_DATE_OUT_OF_RANGE,
    R_NO_START_DATE,
    R_PROVIDER_DISABLED,
    R_UPSTREAM_ERROR,
    R_UPSTREAM_TIMEOUT,
    build_day,
    fetch_forecast,
    forecast_window,
    resolve_city,
)
from app.tools.weather_providers.base import NormalizedDay
from app.tools.weather_providers.open_meteo import weather_text, wind_level

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
NOW = datetime(2026, 11, 23, 22, 40, tzinfo=TZ)


@pytest.fixture(autouse=True)
def _reset_breaker():
    """每个用例都从"熔断器关闭"开始。

    熔断器是有状态的进程级单例，不重置的话「失败用例」会把熔断打开，
    后面的「成功用例」就会莫名其妙拿到 UPSTREAM_ERROR —— 也就是"靠执行顺序才绿"，
    这正是 BUG-017 那一类问题，不能在新代码里再种一次。
    """
    weather_circuit.reset_all()
    yield
    weather_circuit.reset_all()


def _daily(days: int, start: date, **overrides: list[Any]) -> dict[str, list[Any]]:
    """造一份形状合法的 Open-Meteo daily 响应。

    默认给"多云 / 12-16℃ / 降水 6% / 18km/h"，各用例只覆盖自己关心的那列。
    """
    series: dict[str, list[Any]] = {
        "time": [(start + timedelta(days=i)).isoformat() for i in range(days)],
        "weather_code": [2] * days,
        "temperature_2m_max": [16.0] * days,
        "temperature_2m_min": [12.0] * days,
        "temperature_2m_mean": [14.0] * days,
        "precipitation_probability_max": [6] * days,
        "wind_speed_10m_max": [18.0] * days,
    }
    series.update(overrides)
    return series


def _transport(payload: dict | None = None, *, status: int = 200, exc: Exception | None = None, calls: list | None = None):
    """构造一个假上游。`calls` 用来断言"到底有没有发请求"。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(dict(request.url.params))
        if exc is not None:
            raise exc
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _run(coro):
    return asyncio.run(coro)


def _fetch(**kwargs):
    """默认走「有日期 + 已知城市 + 正常上游」，各用例只改自己关心的参数。"""
    start = kwargs.pop("start", date(2026, 11, 24))
    days = kwargs.pop("days", 3)
    payload = kwargs.pop("payload", {"daily": _daily(days, start)})
    transport = kwargs.pop("transport", _transport(payload))
    return _run(
        fetch_forecast(
            kwargs.pop("city", "成都"),
            kwargs.pop("start_date", start.isoformat()),
            days,
            provider=kwargs.pop("provider", "open-meteo"),
            transport=transport,
            now=kwargs.pop("now", NOW),
            **kwargs,
        )
    )


# ------------------------------------------------------------------ 坐标解析


def test_resolve_exact_city():
    coord = resolve_city("成都")
    assert coord is not None
    assert coord.city == "成都"
    assert 30 < coord.lat < 31 and 103 < coord.lon < 105
    assert coord.note == ""


def test_resolve_strips_suffix_and_ethnic_words():
    # 「大理白族自治州」是计划里常见的写法，剥掉后缀与民族名才能命中「大理」
    assert resolve_city("成都市").city == "成都"
    assert resolve_city("大理白族自治州").city == "大理"
    assert resolve_city("西双版纳傣族自治州").city == "西双版纳"


def test_resolve_province_maps_to_capital_with_note():
    coord = resolve_city("云南")
    assert coord is not None and coord.city == "昆明"
    # 映射必须留痕，不能假装精确到"云南"本身
    assert "昆明" in coord.note
    assert resolve_city("云南省").city == "昆明"


def test_resolve_multi_city_takes_first_hit():
    coord = resolve_city("成都/都江堰")
    assert coord is not None
    assert coord.input == "成都/都江堰"
    assert coord.city == "成都"


def test_resolve_unknown_returns_none():
    assert resolve_city("瓦坎达") is None
    assert resolve_city("") is None
    assert resolve_city(None) is None


def test_city_table_is_well_formed():
    """坐标表是手工维护的硬编码数据，格式错误必须在测试里暴露，而不是运行时 KeyError。"""
    raw = json.loads((Path(weather.__file__).with_name("city_coords.json")).read_text(encoding="utf-8"))
    assert len(raw["cities"]) >= 100
    for name, coord in raw["cities"].items():
        assert -90 <= coord["lat"] <= 90, f"{name} 纬度越界"
        assert -180 <= coord["lon"] <= 180, f"{name} 经度越界"
        # 本表只服务国内目的地，经度落在东半球是基本 sanity check
        assert coord["lon"] > 0, f"{name} 经度不在东半球，疑似填错"
    for alias, entry in raw["aliases"].items():
        assert entry["city"] in raw["cities"], f"别名 {alias} 指向了表里没有的 {entry['city']}"


# ------------------------------------------------------------------ 归一化


def test_weather_code_covers_every_open_meteo_code():
    known = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
             71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
    for code in sorted(known):
        assert weather_text(code) is not None, f"WMO {code} 没有中文映射"
    # 不在表里的码不许猜，统一降级成"没拿到"（契约里 value 必须是 null，不能是"未知"）
    assert weather_text(12345) is None
    assert weather_text(None) is None


def test_wind_level_boundaries():
    assert wind_level(0) == 0
    assert wind_level(3.6) == 1      # 1 m/s
    assert wind_level(18.0) == 3     # 5 m/s
    assert wind_level(120.0) == 12   # 上限封顶在 12 级


# ------------------------------------------------------------------ 成功路径


def test_success_produces_three_verified_fact_fields_per_day():
    result = _fetch()
    assert result["ok"] is True
    assert result["source"] == "OPEN_METEO"
    assert "Open-Meteo" in result["attribution"], "CC BY 4.0 要求署名，响应必须带 attribution"
    assert len(result["days"]) == 3

    day = result["days"][0]
    assert day["dayIndex"] == 1
    assert day["date"] == "2026-11-24"
    for key in ("temperature", "precipitation", "wind"):
        field = day[key]
        assert field["source"] == "TOOL"
        assert field["verified"] is True
        # 契约要求 verified=true 时 fetchedAt 必须存在
        assert field["fetchedAt"] == result["fetchedAt"]
        assert field["expiresAt"] == result["expiresAt"]
        assert field["display"]
    assert day["temperature"]["display"] == "12–16℃"
    assert day["temperature"]["value"] == 14.0
    assert "℃" in day["temperature"]["display"]
    assert day["wind"]["display"] == "3级风"


def test_fact_fields_validate_against_the_contract():
    """把产出的 FactField 直接丢进契约的 `factFieldNumber` 校验。

    这是 W-1 里唯一能机器判定的部分：字段名、枚举、allOf 分支全部符合，
    才算"前端拿到的天气是契约内的数据"。
    """
    full = load_schema("travel_plan_v1.json")
    # 拼一个以完整 $defs 为根的文档，让 `#/$defs/...` 内部引用能解析
    validator = Draft202012Validator({"$defs": full["$defs"], **full["$defs"]["factFieldNumber"]})
    result = _fetch()
    for day in result["days"]:
        for key in ("temperature", "precipitation", "wind"):
            errors = list(validator.iter_errors(day[key]))
            assert not errors, f"{key} 不符合 FactField 契约: {[e.message for e in errors]}"


def test_missing_upstream_column_degrades_to_unverified_not_fake():
    """上游少给一列时，该字段必须是"未获取"，不能拿 0 或平均值顶上。"""
    payload = {"daily": _daily(1, date(2026, 11, 24), precipitation_probability_max=[None])}
    result = _fetch(days=1, payload=payload)
    assert result["ok"] is True
    rain = result["days"][0]["precipitation"]
    assert rain["value"] is None
    assert rain["verified"] is False
    assert rain["source"] == "NONE"
    assert rain["fetchedAt"] is None
    assert rain["display"] == "暂无数据"


def test_temperature_falls_back_to_mean_of_min_max_when_mean_missing():
    payload = {"daily": _daily(1, date(2026, 11, 24), temperature_2m_mean=[None])}
    result = _fetch(days=1, payload=payload)
    assert result["days"][0]["temperature"]["value"] == 14.0


def test_alias_city_note_is_carried_into_fields():
    """按省会代表全省时，note 必须跟着字段走，否则前端会以为是省会本身的天气。"""
    result = _fetch(city="云南")
    assert result["resolvedCity"] == "昆明"
    assert "昆明" in result["days"][0]["temperature"]["note"]


# ------------------------------------------------------------------ 降级


def test_no_start_date_never_calls_upstream():
    """日期口径：没有出发日期就查不出"哪天天气"，兜底成今天等于编造（PM 已否决 B）。"""
    calls: list = []
    result = _fetch(start_date=None, transport=_transport({"daily": {}}, calls=calls))
    assert result["ok"] is False
    assert result["reason"] == R_NO_START_DATE
    assert calls == [], "无出发日期时不许发请求，否则等于用别的日期查了天气"


def test_unknown_city_degrades_without_calling_upstream():
    calls: list = []
    result = _fetch(city="瓦坎达", transport=_transport({"daily": {}}, calls=calls))
    assert result == {**result, "ok": False, "reason": R_CITY_UNKNOWN}
    assert calls == []


def test_provider_disabled_short_circuits():
    calls: list = []
    result = _fetch(provider="none", transport=_transport({"daily": {}}, calls=calls))
    assert result["ok"] is False
    assert result["reason"] == R_PROVIDER_DISABLED
    assert calls == []


def test_upstream_timeout_is_classified_as_timeout():
    result = _fetch(transport=_transport(exc=httpx.ReadTimeout("slow")))
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_TIMEOUT


def test_upstream_500_is_classified_as_error():
    result = _fetch(transport=_transport({"error": True}, status=500))
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_ERROR


def test_blocked_by_breaker_is_circuit_open_not_upstream_error():
    """熔断拦住的那一次**没有出网**，reason 必须是 CIRCUIT_OPEN。

    这条和 provider 无关——分支在编排层，两个 provider 共用。
    断言落在"调用次数没涨"上，不靠耗时：注入的失败也是毫秒级，两者从外部同形。
    """
    calls: list = []
    transport = _transport({"error": True}, status=500, calls=calls)
    for _ in range(3):
        assert _fetch(transport=transport)["reason"] == R_UPSTREAM_ERROR
    assert len(calls) == 3
    assert weather_circuit.breaker_for("open-meteo").state == weather_circuit.OPEN

    blocked = _fetch(transport=transport)
    assert blocked["reason"] == R_CIRCUIT_OPEN
    assert len(calls) == 3, "熔断期间禁止出网"


def test_upstream_200_with_broken_shape_is_error_not_empty():
    """上游 200 但结构变了：这是"上游异常"，不能当作"这几天没天气"。"""
    result = _fetch(payload={"daily": {}})
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_ERROR


def test_out_of_window_start_date_degrades():
    window_start, window_end = forecast_window(NOW)
    far = window_end + timedelta(days=3)
    calls: list = []
    result = _fetch(start=far, start_date=far.isoformat(), transport=_transport({"daily": {}}, calls=calls))
    assert result["ok"] is False
    assert result["reason"] == R_DATE_OUT_OF_RANGE
    assert calls == [], "越窗口的请求不该发给上游"


def test_past_trip_degrades_with_distinct_message():
    past = date(2026, 11, 1)
    result = _fetch(start=past, start_date=past.isoformat())
    assert result["ok"] is False
    assert result["reason"] == R_DATE_OUT_OF_RANGE
    assert "过去" in result["message"], "「已过去」与「太远」是两个可解决的问题，文案不能合并"


def test_partial_window_returns_only_in_window_days_with_absolute_index():
    """行程跨过窗口边界时只返回窗口内的天，且 dayIndex 相对整段行程编号。

    dayIndex 若从本次返回的第一天重新算起，前端会把"第 3 天"显示成"第 1 天"。
    """
    today = date(2026, 11, 23)
    start = today - timedelta(days=1)          # 昨天出发，行程 3 天
    payload = {"daily": _daily(3, start)}
    result = _fetch(start=start, start_date=start.isoformat(), days=3, payload=payload)
    assert result["ok"] is True
    assert [d["date"] for d in result["days"]] == ["2026-11-23", "2026-11-24"]
    assert [d["dayIndex"] for d in result["days"]] == [2, 3]
    assert result["note"] and "过去" in result["note"]


def test_request_params_carry_window_and_coordinates():
    calls: list = []
    _fetch(transport=_transport({"daily": _daily(3, date(2026, 11, 24))}, calls=calls))
    params = calls[0]
    assert params["start_date"] == "2026-11-24"
    assert params["end_date"] == "2026-11-26"
    assert params["timezone"] == "Asia/Shanghai"
    assert float(params["latitude"]) == pytest.approx(30.5728, abs=1e-3)


def test_build_day_is_pure_and_matches_helpers():
    """build_day 只吃 `NormalizedDay`，不认识任何上游字段名——这是换源时 Java/Vue 不用动的原因。"""
    coord = resolve_city("成都")
    day = build_day(
        NormalizedDay(
            date=date(2026, 11, 24),
            weather_text="多云",
            temp_max=16.0,
            temp_min=12.0,
            temp_mean=14.0,
            precip_prob=6.0,
            wind_scale=3,
            wind_speed_ms=5.0,
        ),
        1,
        coord,
        "2026-11-23T22:40:00+08:00",
        "2026-11-23T23:40:00+08:00",
        "OPEN_METEO",
    )
    assert day["weather"]["display"] == "多云"
    assert day["temperature"]["display"] == "12–16℃"
    assert day["wind"]["display"] == "3级风"


# ------------------------------------------------------------------ 端点


def test_endpoint_defaults_to_disabled_by_config():
    """仓库默认 `weather.provider: none`，端点必须直接降级、不碰上游。

    这是 W-8 的一半：合规上免费端点禁商用，所以"默认关闭"本身是验收项，
    不能因为开发方便就把它改成 open-meteo。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    resp = client.get("/v1/weather", params={"city": "成都", "start_date": "2026-11-24", "days": 3})
    assert resp.status_code == 200, "拿不到天气不是请求错误，不能返回 4xx/5xx"
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == R_PROVIDER_DISABLED


def test_endpoint_rejects_bad_days_parameter():
    """参数非法（days 越界）才是真的 422——与"拿不到天气"区分开。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/v1/weather", params={"city": "成都", "days": 0}).status_code == 422
    assert client.get("/v1/weather", params={"city": "成都", "days": 99}).status_code == 422

