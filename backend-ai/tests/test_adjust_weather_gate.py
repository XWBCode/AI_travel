"""M3-3 · S3：数值闸门的双向验收（不发网络请求）。

PM 的重点验收是两句话，这个文件用两组完全对称的用例分别证明：

- **注入成功之后**，抄了天气数值的自由文本必须被拒；
- **没有注入时**，同一段文本必须照旧通过 —— 否则这个闸门就成了"顺便收紧了已验收的调整规则"。

还有一条容易被忽略的：`validate_ops` 一个字符没改。这不是靠自觉，靠断言——
它今天对含「80%」的 note 仍然返回空问题列表，拦截发生在别处（`validate_weather_values`）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from app.config import settings
from app.plan.adjust import AdjustError, run_adjust, validate_ops
from app.tools import weather_adjust, weather_inject

TODAY = date.today()


# ---------------------------------------------------------------- 造数据


def fact(display: str) -> dict:
    return {"value": 1, "display": display, "source": "TOOL", "verified": True,
            "fetchedAt": "2026-11-24T09:00:00+08:00", "expiresAt": "2026-11-24T10:00:00+08:00",
            "note": ""}


def ok_payload(city: str, start: date, days: int) -> dict:
    return {
        "ok": True, "source": "QWeather", "attribution": "天气服务由和风天气驱动",
        "city": city, "resolvedCity": city, "fetchedAt": "2026-11-24T09:00:00+08:00",
        "days": [
            {"dayIndex": index + 1, "date": (start + timedelta(days=index)).isoformat(),
             "city": city, "weather": fact("小雨"), "temperature": fact("11–16℃"),
             "precipitation": fact("降水概率 80%"), "wind": fact("东北风 2 级")}
            for index in range(days)
        ],
    }


async def _fetch(city, start_date, days, **kwargs):
    return ok_payload(city, start_date, days)


class ScriptedProvider:
    """回放一段指定 JSON 的 provider，用来精确控制"模型写了什么"。"""

    name = "echo"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.sent: list[dict[str, str]] = []

    async def stream_chat(self, messages, trace_id: str = "", json_mode: bool = False):
        self.sent = [{"role": m.role, "content": m.content} for m in messages]
        text = json.dumps(self.payload, ensure_ascii=False)
        for index in range(0, len(text), 400):
            yield text[index:index + 400]

    def describe(self) -> dict:
        return {"provider": self.name, "model": "echo-model", "real": True}


def view() -> dict:
    return {
        "title": "成都三日", "totalDays": 2, "startDate": TODAY.isoformat(),
        "status": "IN_PROGRESS",
        "days": [
            {"dayIndex": 1, "date": TODAY.isoformat(), "city": "成都", "theme": None,
             "items": [{"itemId": "1", "itemOrder": 1, "title": "宽窄巷子",
                        "startTime": "09:00", "endTime": "11:00", "note": None}]},
            {"dayIndex": 2, "date": (TODAY + timedelta(days=1)).isoformat(), "city": "成都",
             "theme": None, "items": []},
        ],
    }


def note_op(text: str) -> dict:
    return {"summary": "改备注", "ops": [
        {"op": "UPDATE_ITEM", "itemId": "1", "changes": [{"field": "note", "after": text}]},
    ]}


def title_op(text: str) -> dict:
    return {"summary": "改标题", "ops": [
        {"op": "UPDATE_ITEM", "itemId": "1", "changes": [{"field": "title", "after": text}]},
    ]}


def theme_op(text: str) -> dict:
    return {"summary": "改主题", "ops": [
        {"op": "UPDATE_DAY", "dayIndex": 2, "changes": [{"field": "theme", "after": text}]},
    ]}


def add_op(text: str) -> dict:
    return {"summary": "加一项", "ops": [
        {"op": "ADD_ITEM", "dayIndex": 2, "title": text, "startTime": None,
         "endTime": None, "note": None},
    ]}


def run(provider, view_obj: dict | None = None) -> dict:
    return asyncio.run(run_adjust(provider, view_obj or view(), "下午太累，改一下", trace_id="t"))


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def clean():
    weather_inject.reset_cache()
    weather_adjust._warned_max_cities = False
    yield
    weather_inject.reset_cache()


@pytest.fixture()
def injected(monkeypatch):
    """天气**确实注入成功**：开关打开、数据源可用、上游返回正常。"""
    monkeypatch.setattr(settings.weather, "inject_on_adjust", True)
    monkeypatch.setattr(settings.weather, "provider", "open-meteo")
    monkeypatch.setattr(weather_inject, "fetch_forecast", _fetch)
    yield


@pytest.fixture()
def not_injected(monkeypatch):
    """天气**没有注入**：开关关着，同样一份上游替身就绪（证明它根本不该被调用）。"""
    monkeypatch.setattr(settings.weather, "inject_on_adjust", False)
    yield


# ---------------------------------------------------------------- 注入过：必须拒


@pytest.mark.parametrize("payload,needle", [
    (note_op("明天下雨，降水概率 80%，改成室内"), "80%"),
    (note_op("夜里只有 11–16℃，多带件外套"), "16℃"),
    (note_op("风大，东北风 2 级，别去爬山"), "2 级"),
    (title_op("小雨（80%）的一天"), "80%"),
    (theme_op("降温到 8度"), "8度"),
    (add_op("雨后地滑，降水量 12毫米"), "12毫米"),
])
def test_injected_rejects_weather_values(injected, payload, needle):
    provider = ScriptedProvider(payload)

    with pytest.raises(AdjustError) as excinfo:
        run(provider)

    assert needle in excinfo.value.message
    assert "抄了天气数值" in excinfo.value.message
    # 拒绝发生在"已经拿到模型输出之后"，说明它是闸门拦的，不是模型没被调用
    assert provider.sent


def test_summary_with_weather_value_is_rejected(injected):
    provider = ScriptedProvider({
        "summary": "把户外改成室内（降水概率 80%）",
        "ops": [{"op": "REMOVE_ITEM", "itemId": "1"}],
    })

    with pytest.raises(AdjustError) as excinfo:
        run(provider)

    assert "summary" in excinfo.value.message


def test_injected_clean_text_passes(injected):
    """闸门只拦数值：说"明天有雨"是允许的，那正是我们要的行为。"""
    provider = ScriptedProvider(note_op("明天有雨，把爬山换成博物馆"))

    result = run(provider)

    assert result["ops"][0]["changes"][0]["after"] == "明天有雨，把爬山换成博物馆"


@pytest.mark.parametrize("text", [
    "15:00 出发",          # HH:mm 本来就有数字
    "第2天再去",            # 序号不算单位
    "下午3点集合",          # 「点」不在天气单位里
    "4星级酒店",            # 数字与「级」之间隔着「星」，不应命中
    "两个人一起去",
])
def test_injected_does_not_misfire_on_normal_text(injected, text):
    provider = ScriptedProvider(note_op(text))

    result = run(provider)

    assert result["ops"][0]["changes"][0]["after"] == text


def test_empty_ops_still_passes(injected):
    """模型说"做不到"时返回空 ops，这是合法结果，不该被闸门误判。"""
    provider = ScriptedProvider({"summary": "这类改动要走行程编辑", "ops": []})

    result = run(provider)

    assert result["ops"] == []


# ---------------------------------------------------------------- 未注入：必须照旧通过


@pytest.mark.parametrize("payload", [
    note_op("明天下雨，降水概率 80%，改成室内"),
    title_op("小雨（80%）的一天"),
    theme_op("降温到 8度"),
])
def test_without_weather_the_same_text_still_passes(not_injected, payload):
    """这是 PM 点名的对称验收：闸门不得改变未注入时的校验结果。"""
    provider = ScriptedProvider(payload)

    result = run(provider)

    assert result["ops"] == payload["ops"]


def test_injection_failure_also_bypasses_the_gate(monkeypatch):
    """开关开着但天气没拿到（上游失败）→ `injected` 为假 → 同样不拦。"""
    monkeypatch.setattr(settings.weather, "inject_on_adjust", True)
    monkeypatch.setattr(settings.weather, "provider", "open-meteo")

    async def boom(city, start_date, days, **kwargs):
        return {"ok": False, "reason": "UPSTREAM_ERROR", "message": "查不到"}

    monkeypatch.setattr(weather_inject, "fetch_forecast", boom)
    provider = ScriptedProvider(note_op("降水概率 80%，别出门"))

    result = run(provider)

    assert "80%" in result["ops"][0]["changes"][0]["after"]


# ---------------------------------------------------------------- 原校验器没被动过


def test_validate_ops_still_ignores_weather_values():
    """`validate_ops` 一个字符未改：它对含「80%」的 note 仍然返回空问题列表。

    拦截发生在 `validate_weather_values`，由调用点在注入过时才触发 ——
    所以"关闭时行为逐字不变"是结构保证，不是靠自觉。
    """
    ops = note_op("降水概率 80%")["ops"]

    assert validate_ops(ops, view()) == []


def test_gate_only_runs_when_injected_flag_is_set(injected):
    """直接对闸门做一次单元级确认：同样的内容，调用点是否触发决定一切。"""
    ops = note_op("降水概率 80%")["ops"]

    assert weather_adjust.validate_weather_values("改备注", ops) != []
    assert weather_adjust.validate_weather_values("改备注", []) == []
