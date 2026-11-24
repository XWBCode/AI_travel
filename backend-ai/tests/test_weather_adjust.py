"""M3-3 · S2：调整期天气注入的接线测试（不发网络请求）。

PM 对 S2 点了四条证据，这个文件就是它们各自的落点：

1. **默认关闭时，调整 Prompt 与改造前 `==` 全等**。不是"看起来差不多"：
   期望值直接用改造前那段拼接式写出来，逐字比。
2. **跨链路缓存命中**。生成链路刚查过的城市，调整链路应该 0 次出网 —— 两条链路
   共用 `weather_inject` 的那一份短缓存（D-A5 的条件之一）。
3. **1200ms 是总预算，不是每城 1200ms**。三座城同时慢，总耗时仍然只有一份预算；
   而且调小预算**不能**污染生成链路那份配置（用 `dataclasses.replace` 换副本，
   不是改 `settings.weather`）。
4. **任何天气故障都不影响原调整**。超时、全失败、无日期、无城市 → `block=None`，
   `run_adjust` 照常返回合法 ops。

另外盯住白名单：查询用全局区间，但交给模型之前必须按 `(日期, 城市)` 过滤，
否则"计划外的日期"会被模型当成计划里的一天。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from app.config import settings
from app.llm.prompts import build_adjust_messages
from app.plan.adjust import run_adjust
from app.tools import weather_adjust, weather_inject

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


# ---------------------------------------------------------------- 造数据


def fact(display: str) -> dict:
    return {
        "value": 1,
        "display": display,
        "source": "TOOL",
        "verified": True,
        "fetchedAt": "2026-11-24T09:00:00+08:00",
        "expiresAt": "2026-11-24T10:00:00+08:00",
        "note": "",
    }


def ok_payload(city: str, start: date, days: int) -> dict:
    return {
        "ok": True,
        "source": "QWeather",
        "attribution": "天气服务由和风天气驱动",
        "city": city,
        "resolvedCity": city,
        "fetchedAt": "2026-11-24T09:00:00+08:00",
        "days": [
            {
                "dayIndex": index + 1,
                "date": (start + timedelta(days=index)).isoformat(),
                "city": city,
                "weather": fact("小雨"),
                "temperature": fact("11–16℃"),
                "precipitation": fact("降水概率 80%"),
                "wind": fact("东北风 2 级"),
            }
            for index in range(days)
        ],
    }


def fail(reason: str) -> dict:
    return {"ok": False, "reason": reason, "message": "查不到"}


class FakeFetch:
    """按城市返回预设结果，并记录每次调用。`slow` 用来造"超预算"的城市。"""

    def __init__(self, spec: dict[str, str] | None = None) -> None:
        self.spec = spec or {}
        self.calls: list[tuple[str, date, int]] = []

    async def __call__(self, city, start_date, days, **kwargs):
        self.calls.append((city, start_date, days))
        kind = self.spec.get(city, "ok")
        if kind == "slow":
            await asyncio.sleep(5)
            return ok_payload(city, start_date, days)
        if kind == "five_hundred":
            return fail("UPSTREAM_ERROR")
        return ok_payload(city, start_date, days)


class EchoAdjust:
    """回声 provider：记下收到的 messages，回放一条合法的调整建议。"""

    name = "echo"

    def __init__(self, item_id: str = "1") -> None:
        self.item_id = item_id
        self.sent: list[dict[str, str]] = []

    async def stream_chat(self, messages, trace_id: str = "", json_mode: bool = False):
        self.sent = [{"role": m.role, "content": m.content} for m in messages]
        payload = {
            "summary": "把这一项改到下午",
            "ops": [{
                "op": "UPDATE_ITEM",
                "itemId": self.item_id,
                "changes": [{"field": "startTime", "after": "15:00"}],
            }],
        }
        text = json.dumps(payload, ensure_ascii=False)
        for index in range(0, len(text), 400):
            yield text[index:index + 400]

    def describe(self) -> dict:
        return {"provider": self.name, "model": "echo-model", "real": True}


def make_view(days: list[tuple[int, str | None, str]], start: str | None = None) -> dict:
    """造一个与 Java `build()` 同形的可编辑视图。`days` 是 (dayIndex, 日期, 城市)。"""
    return {
        "title": "成都三日",
        "totalDays": len(days),
        "startDate": start,
        "status": "IN_PROGRESS",
        "days": [
            {
                "dayIndex": index,
                "date": day_date,
                "city": city,
                "theme": None,
                "items": [
                    {"itemId": str(index), "itemOrder": 1, "title": "宽窄巷子",
                     "startTime": "09:00", "endTime": "11:00", "note": None},
                ],
            }
            for index, day_date, city in days
        ],
    }


def started_view(cities: list[str] | None = None) -> dict:
    """行程**已经开始**（昨天出发）—— 这是调整链路最典型的形态。

    城市按天轮转填充：单城时三天都是它，两城时前两天成都、第三天乐山。
    """
    cities = cities or ["成都"]
    dates = [YESTERDAY, TODAY, TODAY + timedelta(days=1)]
    picked = [cities[index % len(cities)] for index in range(len(dates))]
    return make_view([
        (index + 1, day.isoformat(), picked[index]) for index, day in enumerate(dates)
    ])


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def clean_cache():
    weather_inject.reset_cache()
    weather_adjust._warned_max_cities = False
    yield
    weather_inject.reset_cache()


@pytest.fixture()
def adjust_on(monkeypatch):
    monkeypatch.setattr(settings.weather, "inject_on_adjust", True)
    monkeypatch.setattr(settings.weather, "provider", "open-meteo")
    yield


@pytest.fixture()
def adjust_off(monkeypatch):
    monkeypatch.setattr(settings.weather, "inject_on_adjust", False)
    yield


def with_fetch(monkeypatch, spec: dict[str, str] | None = None) -> FakeFetch:
    fake = FakeFetch(spec)
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)
    return fake


def adjust(provider, view: dict, text: str = "下午太累，把第一项改到三点") -> dict:
    return asyncio.run(run_adjust(provider, view, text, trace_id="t"))


# ---------------------------------------------------------------- 1. 默认关闭全等


def test_disabled_keeps_adjust_prompt_byte_identical(adjust_off, monkeypatch):
    """期望值直接用**改造前**那段拼接式写出来，不是拿新实现自己比自己。"""
    fake = with_fetch(monkeypatch)
    view = started_view()
    text = "下午太累，把第一项改到三点"

    provider = EchoAdjust()
    adjust(provider, view, text)

    expected = (
        "【当前计划】\n"
        + json.dumps(view, ensure_ascii=False, indent=2)
        + "\n\n【用户要求】\n"
        + text
    )
    assert provider.sent[1]["content"] == expected
    assert fake.calls == []           # 关闭时连上游都不碰


def test_none_block_produces_identical_messages():
    """即使天气模块被调用到，`weather_block=None` 也必须不产生任何多余字符。"""
    view = started_view()
    assert build_adjust_messages(view, "改成三点") == build_adjust_messages(view, "改成三点", None)


def test_mock_provider_never_queries_weather(adjust_on, monkeypatch):
    """mock 分支在查天气之前就返回：单测与本地联调天然零出网。"""
    fake = with_fetch(monkeypatch)

    class MockProvider:
        name = "mock"

        async def stream_chat(self, messages, trace_id: str = "", json_mode: bool = False):
            for piece in ("{}",):
                yield piece

        def describe(self) -> dict:
            return {"provider": "mock", "model": "m", "real": False}

    result = adjust(MockProvider(), started_view())
    assert result["mock"] is True
    assert fake.calls == []


# ---------------------------------------------------------------- 2. 跨链路缓存


def test_generation_cache_is_shared_with_adjust(adjust_on, monkeypatch):
    """生成链路刚查过之后，调整链路同一 (城市, 起点, 天数) 必须 0 次出网。"""
    monkeypatch.setattr(settings.weather, "inject_on_generation", True)
    fake = with_fetch(monkeypatch)

    gen = asyncio.run(weather_inject.prepare_weather_block(
        {"destination": "成都", "destinations": ["成都"],
         "startDate": TODAY.isoformat(), "days": 2},
        trace_id="g",
    ))
    assert gen.upstream == 1

    # 同一座城、同一个区间（今天起两天）——键完全一致，应当直接命中
    view = make_view([(1, TODAY.isoformat(), "成都"), (2, (TODAY + timedelta(days=1)).isoformat(), "成都")])
    block = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="a"))

    assert block.injected is True
    assert block.upstream == 0
    assert len(fake.calls) == 1          # 全程只出网一次


def test_second_identical_adjust_hits_cache(adjust_on, monkeypatch):
    fake = with_fetch(monkeypatch)
    view = started_view()

    first = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="a1"))
    second = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="a2"))

    assert first.upstream == 1
    assert second.upstream == 0
    assert second.elapsed_ms <= 5
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- 3. 预算


def test_default_adjust_switch_and_budget():
    """默认值本身是验收对象：开关必须关，预算必须是 1200（不是复用生成期的 2000）。"""
    from app.config import WeatherSettings

    fresh = WeatherSettings()
    assert fresh.inject_on_adjust is False
    assert fresh.inject_adjust_timeout_ms == 1200
    # 两条链路各自一份预算，互不覆盖
    assert fresh.inject_timeout_ms == 2000


def test_budget_is_total_not_per_city(adjust_on, monkeypatch):
    """三座城同时慢：总耗时是一份预算，不是三份。"""
    monkeypatch.setattr(settings.weather, "inject_adjust_timeout_ms", 300)
    monkeypatch.setattr(settings.weather, "inject_max_cities", 3)
    with_fetch(monkeypatch, {"成都": "slow", "乐山": "slow", "昆明": "slow"})
    view = started_view(["成都", "乐山", "昆明"])

    result = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="t"))

    assert result.injected is False          # 一个都没回来，就当没天气
    assert result.elapsed_ms < 900           # 900 = 三份预算；这里必须只有一份
    assert result.elapsed_ms >= 250


def test_adjust_budget_does_not_leak_into_generation_config(adjust_on, monkeypatch):
    """用 `replace` 换副本，不是改 `settings.weather`：生成链路那份预算必须原样。"""
    monkeypatch.setattr(settings.weather, "inject_adjust_timeout_ms", 200)
    with_fetch(monkeypatch)
    before = settings.weather.inject_timeout_ms

    asyncio.run(weather_adjust.prepare_adjust_weather_block(started_view(), trace_id="t"))

    assert settings.weather.inject_timeout_ms == before == 2000


# ---------------------------------------------------------------- 4. 失败不影响原调整


@pytest.mark.parametrize("spec", [{"成都": "five_hundred"}, {"成都": "slow"}])
def test_weather_failure_still_returns_valid_ops(adjust_on, monkeypatch, spec):
    monkeypatch.setattr(settings.weather, "inject_adjust_timeout_ms", 200)
    with_fetch(monkeypatch, spec)

    provider = EchoAdjust()
    result = adjust(provider, started_view())

    assert set(result) == {"summary", "ops", "mock"}     # 响应字段集不变
    assert result["ops"][0]["op"] == "UPDATE_ITEM"
    # 没有天气时 prompt 与改造前一致：说明失败被消化成 block=None，而不是塞了半张表
    assert provider.sent[1]["content"].startswith("【当前计划】")


def test_partial_success_only_injects_the_successful_city(adjust_on, monkeypatch):
    with_fetch(monkeypatch, {"乐山": "five_hundred"})
    view = started_view(["成都", "乐山"])

    result = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="t"))

    assert result.injected is True
    assert "成都" in result.block
    assert "请勿为它们编造天气：乐山" in result.block
    assert "| 乐山 |" not in result.block           # 没数据的城市不得出现在表里


def test_no_date_means_no_upstream_call(adjust_on, monkeypatch):
    fake = with_fetch(monkeypatch)
    view = make_view([(1, None, "成都"), (2, None, "成都")], start=None)

    result = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="t"))

    assert result.injected is False
    assert result.reason == "NO_START_DATE"
    assert fake.calls == []


def test_no_city_means_no_upstream_call(adjust_on, monkeypatch):
    fake = with_fetch(monkeypatch)
    view = make_view([(1, TODAY.isoformat(), " "), (2, TODAY.isoformat(), None)])

    result = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="t"))

    assert result.reason == "NO_CITY"
    assert fake.calls == []


def test_finished_trip_means_no_upstream_call(adjust_on, monkeypatch):
    """全部已过去：交集的上下界直接空掉，零出网。"""
    fake = with_fetch(monkeypatch)
    view = make_view([(1, (TODAY - timedelta(days=5)).isoformat(), "成都")])

    result = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="t"))

    assert result.reason == "DATE_OUT_OF_RANGE"
    assert fake.calls == []


# ---------------------------------------------------------------- 注入形态与白名单


def test_block_goes_first_and_before_the_user_words(adjust_on, monkeypatch):
    with_fetch(monkeypatch)
    provider = EchoAdjust()
    text = "下午太累，把第一项改到三点"
    adjust(provider, started_view(), text)

    content = provider.sent[1]["content"]
    assert content.startswith("【已核实的天气预报】")
    assert content.index("【已核实的天气预报】") < content.index("【当前计划】") < content.index("【用户要求】")
    # 用户原话逐字保留在最后（总纲 8.1：不得被拼进别的段落）
    assert content.endswith("【用户要求】\n" + text)
    assert "【本次是调整，不是重排】" in content


def test_whitelist_drops_dates_outside_the_plan(adjust_on, monkeypatch):
    """计划只有今天和第四天，查询区间覆盖这四天：多查的两天绝不能进 prompt。"""
    fake = with_fetch(monkeypatch)
    later = TODAY + timedelta(days=3)
    view = make_view([
        (1, TODAY.isoformat(), "成都"),
        (2, later.isoformat(), "成都"),
    ], start=None)

    result = asyncio.run(weather_adjust.prepare_adjust_weather_block(view, trace_id="t"))

    assert result.injected is True
    assert fake.calls[0][2] == 4                      # 区间确实是四天
    assert "| " + TODAY.isoformat() + " |" in result.block
    assert "| " + later.isoformat() + " |" in result.block
    for skipped in (TODAY + timedelta(days=1), TODAY + timedelta(days=2)):
        assert skipped.isoformat() not in result.block


def test_city_with_no_rows_left_becomes_uncovered():
    """整座城市的行都被白名单过滤掉时，宁可说"没有"，也不让它静默消失。"""
    from app.tools.weather_brief import build_brief

    brief = build_brief([("成都", ok_payload("成都", TODAY + timedelta(days=5), 2))])
    assert brief is not None

    filtered = weather_adjust.filter_brief(brief, ((TODAY.isoformat(), "成都"),))

    assert filtered is None or "成都" in filtered.uncovered_cities


def test_max_cities_zero_logs_a_config_warning(adjust_on, monkeypatch, caplog):
    monkeypatch.setattr(settings.weather, "inject_max_cities", 0)
    with_fetch(monkeypatch)

    with caplog.at_level("WARNING", logger="ai-travel.weather"):
        asyncio.run(weather_adjust.prepare_adjust_weather_block(started_view(), trace_id="t"))

    assert any("WEATHER_INJECT_MAX_CITIES" in record.message for record in caplog.records)
