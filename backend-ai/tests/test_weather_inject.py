"""M3-2 · S2：生成期天气注入的接线测试（不发网络请求）。

PM 对 S2 额外点了三条验收，这个文件就是它们各自的证据：

1. **默认关闭时行为与原版一致**。不是"看起来差不多"，而是同一份 history 组出来的
   messages 逐字相同、上游调用次数恒为 0。
2. **坐标未知的城市不发起上游请求**。这条最容易做成"发起了、失败了、被当成没数据"——
   外观一样，但额度和风控都白花了。所以断言下到 provider 的 `fetch` 那一层，
   并证明 `fetch_forecast` 在解析不出坐标时**根本没走到那里**。
3. **多城部分成功时，Prompt 不得把未覆盖城市描述成已有天气数据**。表里只出现有数据的城市，
   没有的城市进"请勿为它们编造天气"那一句。

另外还盯住两件"错了也看不出来"的事：天气失败不能阻塞生成；熔断/失败不做重试。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from app.config import WeatherSettings, settings
from app.llm.prompts import build_plan_messages
from app.plan import mock
from app.plan.generator import run_generation
from app.plan.intent import analyze, analyze_messages
from app.tools import weather_inject
from app.tools.weather_brief import SKIP_NO_DATA

# 出发日必须在预报窗口内，否则 can_inject 会直接跳过（那是 S1 已经测过的边界）。
# 用相对日期而不是写死 2026-11-24：写死的话这个文件会在那一天之后集体变红。
START_DATE = date.today() + timedelta(days=2)
START = START_DATE.isoformat()
# 用户原话里必须**带日期**：没有 startDate 就没有"哪天的天气"，那是最常见的跳过原因
_DATE_CN = f"{START_DATE.month}月{START_DATE.day}日"
USER_TEXT = f"帮我规划成都三天，{_DATE_CN}出发，住在市中心，喜欢美食和历史"
# 双城写法：「成都和昆明四天」会被规则抽成 destinations=[成都, 昆明]、days=4
MULTI_TEXT = f"帮我规划成都和昆明四天，{_DATE_CN}出发，喜欢美食"


# ---------------------------------------------------------------- 造数据


def fact(display: str) -> dict:
    """与 M3-1 `build_day` 同形的 FactField（已核实）。"""
    return {
        "value": 1,
        "display": display,
        "source": "TOOL",
        "verified": True,
        "fetchedAt": "2026-11-24T09:00:00+08:00",
        "expiresAt": "2026-11-24T10:00:00+08:00",
        "note": "",
    }


def ok_payload(city: str, days: int = 3) -> dict:
    """`fetch_forecast` 成功时的返回形状（只取注入用得上的字段）。"""
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
                "date": (START_DATE + timedelta(days=index)).isoformat(),
                "city": city,
                "weather": fact("多云"),
                "temperature": fact("11–16℃"),
                "precipitation": fact("降水概率 30%"),
                "wind": fact("2级风"),
            }
            for index in range(days)
        ],
    }


def fail(reason: str) -> dict:
    return {"ok": False, "reason": reason, "message": "查不到"}


class FakeFetch:
    """替身：按城市返回预设结果，并记录每次调用（城市与天数）。"""

    def __init__(self, results: dict[str, dict | None]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, city, start_date, days, **kwargs):
        self.calls.append((city, days))
        result = self.results.get(city)
        return result if result is not None else fail("CITY_UNKNOWN")


class EchoProvider:
    """回声 provider：把收到的 messages 存下来，回放一段固定的计划 JSON。

    用它而不是真模型，是为了断言注入块"真的进了最终 prompt"——
    这是端到端验证里唯一无法用单测替代的一环。
    """

    name = "echo"

    def __init__(self, plan: dict) -> None:
        self._plan = plan
        self.sent: list[dict[str, str]] = []
        self.last_finish_reason = "stop"

    async def stream_chat(self, messages, trace_id: str = "", json_mode: bool = False):
        self.sent = [{"role": m.role, "content": m.content} for m in messages]
        text = json.dumps(self._plan, ensure_ascii=False)
        for index in range(0, len(text), 400):
            yield text[index:index + 400]

    def describe(self) -> dict:
        return {"provider": self.name, "model": "echo-model", "real": True}


@pytest.fixture()
def injection_on(monkeypatch):
    """打开注入开关并清缓存，结束后还原。"""
    monkeypatch.setattr(settings.weather, "inject_on_generation", True)
    monkeypatch.setattr(settings.weather, "provider", "open-meteo")
    weather_inject.reset_cache()
    yield
    weather_inject.reset_cache()


@pytest.fixture()
def injection_off(monkeypatch):
    monkeypatch.setattr(settings.weather, "inject_on_generation", False)
    weather_inject.reset_cache()
    yield
    weather_inject.reset_cache()


def slots(cities: list[str], *, days: int = 3) -> dict:
    return {
        "destination": cities[0],
        "destinations": cities,
        "startDate": START,
        "endDate": (START_DATE + timedelta(days=days - 1)).isoformat(),
        "days": days,
    }


def generate(provider, text: str = USER_TEXT, intent=None):
    history = [{"role": "user", "content": text}]
    intent = intent or analyze_messages(history)

    async def collect():
        return [event async for event in run_generation(provider, intent, history, trace_id="t")]

    return asyncio.run(collect())


def draft_of(events) -> dict:
    draft = next((data for name, data in events if name == "plan_draft"), None)
    assert draft is not None, [name for name, _ in events]
    return draft["plan"]


def messages_of(provider) -> list[dict[str, str]]:
    assert provider.sent, "provider 没被调用"
    return provider.sent


def weather_keys(node, path="") -> list[str]:
    """递归找计划里是否出现了天气字段（AC-3：天气只进 prompt，不进计划）。"""
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("weather", "temperature", "precipitation"):
                hits.append(f"{path}/{key}" if path else key)
            hits.extend(weather_keys(value, f"{path}/{key}" if path else key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(weather_keys(value, f"{path}[{index}]"))
    return hits


# ---------------------------------------------------------------- 开关：默认关


def test_switch_defaults_to_off():
    """默认值必须是关：外部依赖没被显式打开，就不该在生成链路里悄悄生效。"""
    assert WeatherSettings().inject_on_generation is False


def test_switch_off_makes_zero_upstream_calls(monkeypatch, injection_off):
    fake = FakeFetch({"成都": ok_payload("成都")})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)

    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))

    assert result.block is None
    assert result.reason == weather_inject.SKIP_INJECT_DISABLED
    assert fake.calls == [], "开关关闭时一次上游都不该碰"


def test_switch_off_prompt_is_byte_identical_to_baseline(injection_off):
    """关闭时组出来的 messages 必须与"没有这个功能"时完全一致。"""
    history = [
        {"role": "user", "content": "我想去成都"},
        {"role": "assistant", "content": "好的，请问去几天？"},
        {"role": "user", "content": USER_TEXT},
    ]
    without = build_plan_messages(history)
    with_none = build_plan_messages(history, None)
    with_empty = build_plan_messages(history, "")

    assert with_none == without
    assert with_empty == without
    assert without[-1]["content"] == USER_TEXT, "用户原话必须逐字保留，不能加任何前缀"


def test_switch_off_generation_matches_baseline_prompt(injection_off):
    """真跑一遍生成：provider 收到的 messages 与基线一致，且没有注入块。"""
    intent = analyze_messages([{"role": "user", "content": USER_TEXT}])
    provider = EchoProvider(mock.build_plan(intent))

    events = generate(provider, intent=intent)
    sent = messages_of(provider)

    assert sent == build_plan_messages([{"role": "user", "content": USER_TEXT}])
    assert not any("已核实的天气预报" in m["content"] for m in sent)
    assert "预报" not in draft_of(events)["aiNote"]


# ---------------------------------------------------------------- 不花冤枉的额度


def test_disabled_provider_skips_without_querying(monkeypatch, injection_on):
    monkeypatch.setattr(settings.weather, "provider", "none")
    fake = FakeFetch({"成都": ok_payload("成都")})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)

    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))

    assert result.block is None
    assert result.reason == "PROVIDER_DISABLED"
    assert fake.calls == []


def test_no_start_date_makes_no_upstream_call(monkeypatch, injection_on):
    fake = FakeFetch({"成都": ok_payload("成都")})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)

    result = asyncio.run(
        weather_inject.prepare_weather_block({"destination": "成都", "destinations": ["成都"], "days": 3})
    )

    assert result.block is None
    assert result.reason == "NO_START_DATE"
    assert fake.calls == []


def test_unknown_city_never_reaches_provider(monkeypatch, injection_on):
    """坐标未知的城市必须在 `resolve_city` 就被拦下，一个请求都不发。

    断言下到 provider 的 `fetch`（唯一出网点）：如果哪天有人"顺手"在注入侧先查一遍坐标、
    或者把 CITY_UNKNOWN 也当成"查了但失败"，这条会立刻变红。
    """
    from app.tools.weather_providers import open_meteo

    calls: list[dict] = []

    async def boom(**kwargs):
        calls.append(kwargs)
        raise AssertionError("坐标未知的城市不该发起上游请求")

    monkeypatch.setattr(open_meteo, "fetch", boom)
    monkeypatch.setattr(settings.weather, "provider", "open-meteo")

    result = asyncio.run(weather_inject.prepare_weather_block(slots(["阿斯加德"])))

    assert result.block is None
    assert result.reason == SKIP_NO_DATA
    assert result.upstream == 0, "CITY_UNKNOWN 是本地拦下的，不能算成一次上游调用"
    assert calls == []


def test_failed_city_is_not_retried(monkeypatch, injection_on):
    """熔断/上游失败都只查一次。重试会把"持续错误请求"变成对方的封号理由。"""
    fake = FakeFetch({"成都": fail("CIRCUIT_OPEN")})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)

    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))

    assert result.block is None
    assert fake.calls == [("成都", 3)], "一个城市一次，不许重试"
    assert result.upstream == 0, "熔断是本地拦下的"


def test_short_cache_avoids_repeat_upstream(monkeypatch, injection_on):
    """用户连点"重新生成"不该重复打上游（D5）。"""
    fake = FakeFetch({"成都": ok_payload("成都")})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)

    first = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))
    second = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))

    assert first.block and second.block
    assert first.upstream == 1
    assert second.upstream == 0
    assert fake.calls == [("成都", 3)]


# ---------------------------------------------------------------- 注入内容


def test_injected_block_and_note_reach_final_prompt(monkeypatch, injection_on):
    """能查到 → 注入块进最终 prompt、aiNote 说明参考了哪份预报、计划里没有天气字段。"""
    monkeypatch.setattr(weather_inject, "fetch_forecast", FakeFetch({"成都": ok_payload("成都")}))
    intent = analyze_messages([{"role": "user", "content": USER_TEXT}])
    provider = EchoProvider(mock.build_plan(intent))

    events = generate(provider, intent=intent)
    sent = messages_of(provider)
    last_user = sent[-1]["content"]

    assert "【已核实的天气预报】" in last_user
    assert START in last_user
    assert "覆盖城市：成都" in last_user
    # 用户原话仍然逐字在最后，且与注入块分段
    assert last_user.rstrip().endswith(USER_TEXT)
    assert "【用户需求】" in last_user
    # system prompt 没被塞进运行时数据
    assert "已核实的天气预报" not in sent[0]["content"]

    plan = draft_of(events)
    assert "预报" in plan["aiNote"] and "成都" in plan["aiNote"]
    assert weather_keys(plan) == [], "天气只进 prompt，计划契约里不许出现天气字段"


def test_partial_city_failure_lists_uncovered_city(monkeypatch, injection_on):
    """成都成功、昆明查不到 → 表里只有成都，且明确写出昆明没有预报。"""
    monkeypatch.setattr(
        weather_inject,
        "fetch_forecast",
        FakeFetch({"成都": ok_payload("成都")}),
    )
    intent = analyze_messages([{"role": "user", "content": MULTI_TEXT}])
    assert intent.slots["destinations"] == ["成都", "昆明"], intent.slots

    provider = EchoProvider(mock.build_plan(intent))
    events = generate(provider, text=MULTI_TEXT, intent=intent)
    last_user = messages_of(provider)[-1]["content"]

    assert "覆盖城市：成都。" in last_user
    assert "以下城市本次没有拿到预报，请勿为它们编造天气：昆明。" in last_user
    # 表格数据行（含天气文字的那些）里只能出现有数据的城市：
    # 给昆明画一行哪怕是「—」也等于在暗示"查过了、就是这么个天气"
    rows = [line for line in last_user.splitlines() if line.startswith("|") and "多云" in line]
    assert rows and all("成都" in line for line in rows)
    assert not any("昆明" in line for line in rows)

    assert "昆明未取得预报" in draft_of(events)["aiNote"]


def test_all_cities_failed_means_no_injection(monkeypatch, injection_on):
    monkeypatch.setattr(
        weather_inject,
        "fetch_forecast",
        FakeFetch({"成都": fail("UPSTREAM_ERROR"), "昆明": fail("UPSTREAM_ERROR")}),
    )
    intent = analyze_messages([{"role": "user", "content": USER_TEXT}])
    provider = EchoProvider(mock.build_plan(intent))

    events = generate(provider, intent=intent)
    sent = messages_of(provider)

    assert not any("已核实的天气预报" in m["content"] for m in sent)
    assert "预报" not in draft_of(events)["aiNote"]
    assert draft_of(events)["aiNote"], "原有的 aiNote 不能被清空"


def test_upstream_failure_does_not_block_generation(monkeypatch, injection_on):
    """天气挂了，生成必须照常完成——降级不是错误（03 §4）。"""
    monkeypatch.setattr(weather_inject, "fetch_forecast", FakeFetch({"成都": fail("UPSTREAM_ERROR")}))
    intent = analyze_messages([{"role": "user", "content": USER_TEXT}])
    provider = EchoProvider(mock.build_plan(intent))

    events = generate(provider, intent=intent)

    assert "plan_draft" in [name for name, _ in events]
    assert "error" not in [name for name, _ in events]
    assert weather_keys(draft_of(events)) == []


def test_timeout_budget_uses_partial_results(monkeypatch, injection_on):
    """慢城不该拖垮快城：超预算时取消慢的，用已经回来的那部分。"""
    monkeypatch.setattr(settings.weather, "inject_timeout_ms", 100)

    async def slow_or_fast(city, start_date, days, **kwargs):
        if city == "昆明":
            await asyncio.sleep(5)
            return ok_payload("昆明")
        return ok_payload("成都")

    monkeypatch.setattr(weather_inject, "fetch_forecast", slow_or_fast)

    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都", "昆明"])))

    assert result.block is not None
    assert "覆盖城市：成都。" in result.block
    assert "请勿为它们编造天气：昆明。" in result.block


# ---------------------------------------------------------------- aiNote 附注的自保


def test_attach_note_skips_when_it_would_break_contract(monkeypatch, injection_on):
    """附注不合 freeText 就不加——不能因为一句附注把合格的计划判成不合格。"""
    fake = FakeFetch({"成都": ok_payload("成都")})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)
    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))
    assert result.brief is not None

    plan = {"aiNote": "x" * 495}
    assert weather_inject.attach_ai_note(plan, result.brief) is False
    assert plan["aiNote"] == "x" * 495, "超长时不能截断或覆盖原值"


def test_attach_note_skips_numeric_units(monkeypatch, injection_on):
    """城市名里带"数字+单位"时宁可放弃附注：那是契约明令禁止的写法。"""
    monkeypatch.setattr(weather_inject, "fetch_forecast", FakeFetch({"成都30度": ok_payload("成都30度")}))
    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都30度"])))
    assert result.brief is not None

    plan = {"aiNote": "原说明"}
    assert weather_inject.attach_ai_note(plan, result.brief) is False
    assert plan["aiNote"] == "原说明"


def test_attach_note_appends_after_model_text(monkeypatch, injection_on):
    monkeypatch.setattr(weather_inject, "fetch_forecast", FakeFetch({"成都": ok_payload("成都")}))
    result = asyncio.run(weather_inject.prepare_weather_block(slots(["成都"])))

    plan = {"aiNote": "模型写的说明"}
    assert weather_inject.attach_ai_note(plan, result.brief) is True
    assert plan["aiNote"].startswith("模型写的说明 ")
    assert "预报" in plan["aiNote"]


# ---------------------------------------------------------------- 查询范围（PM 四条规则）


def test_days_rule_takes_itinerary_length(monkeypatch, injection_on):
    fake = FakeFetch({"成都": ok_payload("成都", days=5)})
    monkeypatch.setattr(weather_inject, "fetch_forecast", fake)

    asyncio.run(weather_inject.prepare_weather_block(slots(["成都"], days=5)))

    assert fake.calls == [("成都", 5)]
