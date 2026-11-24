"""M3-3 · S4-1 端到端故障矩阵：任何天气故障都不能让原有调整失败。

    APP_ENV=dev WEATHER_PROVIDER=open-meteo \
    .venv/Scripts/python.exe scripts/verify_adjust_weather_matrix.py

## 覆盖范围（PM 点名 12 类，逐条对应）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 天气功能关闭 | 0 上游调用；Prompt 与改造前逐字相同 |
| 2 | 计划无日期 | 0 上游调用、无注入块 |
| 3 | 计划无城市 | 0 上游调用、无注入块 |
| 4 | 行程全部已过去 | 0 上游调用、无注入块 |
| 5 | 全部城市查询成功 | 注入块覆盖全部城市 |
| 6 | 部分城市失败 | 只注入成功的城市，失败城市写进"请勿编造" |
| 7 | 全部城市失败 | 无注入块 |
| 8 | 上游超时（一城慢） | 快的照常注入，慢的进"没有预报"，总耗时不超预算 |
| 9 | 上游返回异常数据 | 视为没数据，**绝不**把「暂无数据」抄进表 |
| 10 | 日期白名单过滤 | 计划外的日期不出现在表里 |
| 11 | 并发调整冲突 | 两个并发请求互不影响；**不自动重试**（provider 调用次数 == 请求数） |
| 12 | 短缓存命中 / 过期 | 第二次 0 上游；过期后重新查一次 |

**全局不变式**（每一场都断言）：`code=0` 且 `ops` 通过 `validate_ops`。
天气是可选上下文，它挂了不能让用户拿不到调整建议。

## 两节"记录型"检查

- **13. 中文数字（AC-A10）**：设计上**不拦**「十几度」「三四级」。这里断言"确实按设计放行"，
  而不是假装拦住了 —— 记成 PASS 是因为期望值就是"放行"，报告里会再写一次。
- **14. 闸门触发率（AC-A12）**：分三组统计真阳 / 假阳 ——
  未注入 + 违规文本（应 0 次拦截）、注入 + 违规文本（应全拦）、注入 + 干净文本（应 0 次拦截）。

## 15~19：设计稿 §S4 表里另外五种"挡在出网之前"的情形

上面 12 类来自 PM 点名的故障矩阵，但设计稿 §S4 的 12 行还包含四种**本地判定**场景
（不是上游故障）：数据源未启用、城市无坐标、行程在 10 天外、行程跨今天。
它们的共同判据是「不该出网就一次都不出」，所以单独成段、逐条断言上游调用数：

| # | 场景 | 期望 |
|---|---|---|
| 15 | 数据源未启用（`provider=none`） | `PROVIDER_DISABLED`，0 上游，无注入块 |
| 16 | 城市无坐标（表里查不到） | `CITY_UNKNOWN`，**0 上游**（坐标未知不得出网） |
| 17 | 行程全在 10 天外 | `DATE_OUT_OF_RANGE`，0 上游 |
| 18 | 行程跨今天（`startDate=今天-2`，共 5 天） | 注入，且**只有今天起的天**（AC-A6） |
| 19 | 两城分段（成都 2 天 + 乐山 2 天） | 每城只绑自己的日期（AC-A7） |
| 20 | 响应结构与键集合（AC-A4） | 与"未注入"基线**相等**，递归无 weather 类键 |

## 判据里为什么没有 SKIP

所有场景都在进程内可造（替身 + 开关），不依赖外网与凭据。跑完应 0 SKIP。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["APP_ENV"] = "dev"
os.environ["WEATHER_PROVIDER"] = "open-meteo"
os.environ["LLM_PROVIDER"] = "mock"
os.environ.pop("WEATHER_INJECT_ON_ADJUST", None)
os.environ.pop("WEATHER_INJECT_ON_GENERATION", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.plan.adjust import validate_ops  # noqa: E402
from app.tools import weather_circuit, weather_inject  # noqa: E402
from app.tools.weather_providers import open_meteo  # noqa: E402
import wx_echo as w  # noqa: E402

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)

# 乐山纬度明显低于成都，用坐标把"这一城慢/失败"分流出去
LESHAN_LAT = 29.5521
CHENGDU_LAT = 30.5728

RESULTS: list[tuple[str, str, str]] = []

# AC-A4：响应里不许出现任何天气结构化字段。天气只以 Prompt 文本的形式存在，
# 一旦出现在响应里，前端就能"顺便展示"，绕过 03 §4.1「调整期不落痕」。
FORBIDDEN_RESPONSE_KEYS = ("weather", "temperature", "precipitation", "wind",
                          "humidity", "forecast", "tempRange", "precipProb")


class ElapsedProbe(logging.Handler):
    """从 `weather_adjust_injected` 埋点里取每次请求的链路内天气耗时。

    AC-A9 要求"第二次 weather_ms ≤ 5"，而客户端墙钟里混着模型与 HTTP 开销，
    只有埋点的 `elapsedMs` 才是天气本身占的时间。用日志当数据源而不是改产品代码，
    是因为这个脚本的前提就是"产品代码零改动"。
    """

    def __init__(self) -> None:
        super().__init__()
        self.by_trace: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 —— 日志格式变了不该让验证脚本崩
            return
        if not message.startswith("weather_adjust_injected"):
            return
        fields = dict(item.split("=", 1) for item in message.split() if "=" in item)
        try:
            self.by_trace[fields.get("trace", "")] = int(fields.get("elapsedMs", "-1"))
        except ValueError:
            pass


def weather_keys_in(value, path: str = "data") -> list[str]:
    """递归找出响应里出现的天气类键（AC-A4）。"""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {item.lower() for item in FORBIDDEN_RESPONSE_KEYS}:
                found.append(f"{path}.{key}")
            found.extend(weather_keys_in(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(weather_keys_in(child, f"{path}[{index}]"))
    return found


def check(item: str, ok: bool, detail: str, *, skip: bool = False) -> bool:
    status = "SKIP" if skip else ("PASS" if ok else "FAIL")
    RESULTS.append((item, status, detail))
    print(f"[{status}] {item} —— {detail}", flush=True)
    return ok


def by_lat(mode: str, lat_ref: float = LESHAN_LAT) -> "callable":
    return lambda lat, lon: mode if abs(lat - lat_ref) < 0.5 else w.MODE_OK


# ---------------------------------------------------------------- 造计划与建议


def plan_of(cities: list[str], dates: list[str] | None = None, *, start: str | None = None) -> dict:
    """与 Java `build()` 同形的可编辑视图。"""
    dates = dates or [YESTERDAY.isoformat(), TODAY.isoformat(), TOMORROW.isoformat()]
    picked = [cities[index % len(cities)] for index in range(len(dates))]
    return {
        "title": "成都三日",
        "totalDays": len(dates),
        "startDate": start,
        "status": "IN_PROGRESS",
        "days": [
            {
                "dayIndex": index + 1,
                "date": day,
                "city": picked[index],
                "theme": None,
                "items": [{"itemId": str(index + 1), "itemOrder": 1, "title": "宽窄巷子",
                           "startTime": "09:00", "endTime": "11:00", "note": None}],
            }
            for index, day in enumerate(dates)
        ],
    }


def suggest(note: str | None = None, summary: str = "把第一项挪到下午") -> dict:
    """一条合法的调整建议；`note` 用来造"抄了天气数值"的违规输出。"""
    changes = [{"field": "startTime", "after": "15:00"}]
    if note is not None:
        changes = [{"field": "note", "after": note}]
    return {"summary": summary, "ops": [
        {"op": "UPDATE_ITEM", "itemId": "2", "changes": changes},
    ]}


class Runner:
    def __init__(self, client: TestClient, stub: w.WeatherStub) -> None:
        self.client = client
        self.stub = stub
        self.responses: list[dict] = []      # AC-A4 拿全部响应做结构比对

    def adjust(
        self,
        plan: dict,
        *,
        inject: bool,
        payload: dict | None = None,
        timeout_ms: int | None = None,
        cache_seconds: int | None = None,
        reset_cache: bool = True,
        trace: str = "t",
    ) -> tuple[dict, w.EchoProvider, float]:
        settings.weather.inject_on_adjust = inject
        if timeout_ms is not None:
            settings.weather.inject_adjust_timeout_ms = timeout_ms
        if cache_seconds is not None:
            settings.weather.inject_cache_seconds = cache_seconds
        if reset_cache:
            weather_inject.reset_cache()
        weather_circuit.reset_all()          # 熔断是进程级单例，上一场开着就会假跳过
        self.stub.reset_calls()

        provider = w.EchoProvider(payload or suggest())
        app.state.provider = provider
        started = time.perf_counter()
        body = self.client.post(
            "/v1/plan/adjust",
            json={"instruction": "下午太累，改到三点", "plan": plan},
            headers={"X-Trace-Id": trace},
        ).json()
        self.responses.append(body)
        return body, provider, (time.perf_counter() - started) * 1000


def user_text(provider: w.EchoProvider) -> str:
    return w.last_user(provider.sent)


# ---------------------------------------------------------------- 主流程


def main() -> int:
    print(f"今天 {TODAY}", flush=True)
    app.state.provider = w.EchoProvider(suggest())
    client = TestClient(app)
    stub = w.WeatherStub()
    open_meteo.fetch = stub
    run = Runner(client, stub)
    probe = ElapsedProbe()
    logging.getLogger("ai-travel.weather").addHandler(probe)

    def ok_body(body: dict, plan: dict) -> bool:
        data = body.get("data") or {}
        return (body.get("code") == 0
                and not validate_ops(data.get("ops"), plan)
                and bool(data.get("ops")))

    base_plan = plan_of(["成都"])

    # ---------------------------------------------------------------- 1 关闭
    print("\n=== 1 天气功能关闭 ===", flush=True)
    body, provider, _ = run.adjust(base_plan, inject=False)
    check("1.1 关闭时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("1.2 关闭时仍返回合法建议", ok_body(body, base_plan), f"code={body.get('code')}")
    expected = ("【当前计划】\n" + json.dumps(base_plan, ensure_ascii=False, indent=2)
                + "\n\n【用户要求】\n下午太累，改到三点")
    check("1.3 关闭时 Prompt 与改造前逐字相同", user_text(provider) == expected,
          "与拼接式期望值 == 全等")

    # ---------------------------------------------------------------- 2 无日期
    print("\n=== 2 计划无日期 ===", flush=True)
    no_date = plan_of(["成都"], dates=[None, None, None], start=None)
    body, provider, _ = run.adjust(no_date, inject=True)
    check("2.1 无日期时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("2.2 无日期时仍返回合法建议", ok_body(body, no_date), f"code={body.get('code')}")
    check("2.3 无日期时 Prompt 无天气块", "【已核实的天气预报】" not in user_text(provider),
          "无注入块")

    # ---------------------------------------------------------------- 3 无城市
    print("\n=== 3 计划无城市 ===", flush=True)
    no_city = plan_of([""], dates=[YESTERDAY.isoformat(), TODAY.isoformat(), TOMORROW.isoformat()])
    for day in no_city["days"]:
        day["city"] = None
    body, _, _ = run.adjust(no_city, inject=True)
    check("3.1 无城市时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("3.2 无城市时仍返回合法建议", ok_body(body, no_city), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 4 全部过去
    print("\n=== 4 行程全部已过去 ===", flush=True)
    past = plan_of(["成都"], dates=[(TODAY - timedelta(days=5)).isoformat(),
                                    (TODAY - timedelta(days=4)).isoformat()])
    body, _, _ = run.adjust(past, inject=True)
    check("4.1 全部过去时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("4.2 全部过去时仍返回合法建议", ok_body(body, past), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 5 全成功
    print("\n=== 5 全部城市查询成功（两城）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    two_city = plan_of(["成都", "乐山"])
    body, provider, _ = run.adjust(two_city, inject=True)
    text = user_text(provider)
    check("5.1 注入块出现", "【已核实的天气预报】" in text, "有注入块")
    check("5.2 两城都在表里", "| 成都 |" in text and "| 乐山 |" in text, "成都、乐山各占行")
    check("5.3 注入块在计划之前、用户原话在最后",
          text.index("【已核实的天气预报】") < text.index("【当前计划】") < text.index("【用户要求】")
          and text.endswith("【用户要求】\n下午太累，改到三点"), "顺序正确")
    check("5.4 仍返回合法建议", ok_body(body, two_city), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 6 部分失败
    print("\n=== 6 部分城市失败（乐山 500）===", flush=True)
    stub.reset(mode=w.MODE_OK, router=by_lat(w.MODE_500))
    body, provider, _ = run.adjust(two_city, inject=True)
    text = user_text(provider)
    check("6.1 成功的城市在表里", "| 成都 |" in text, "成都入表")
    check("6.2 失败的城市写进'请勿编造'", "请勿为它们编造天气：乐山" in text, "乐山进未覆盖")
    check("6.3 失败的城市不出现在表里", "| 乐山 |" not in text, "无乐山行")
    check("6.4 仍返回合法建议", ok_body(body, two_city), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 7 全部失败
    print("\n=== 7 全部城市失败 ===", flush=True)
    stub.reset(mode=w.MODE_500)
    body, provider, _ = run.adjust(two_city, inject=True)
    check("7.1 全部失败时无注入块", "【已核实的天气预报】" not in user_text(provider), "无注入块")
    check("7.2 全部失败时仍返回合法建议", ok_body(body, two_city), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 8 超时
    print("\n=== 8 上游超时（乐山慢，预算 300ms）===", flush=True)
    stub.reset(mode=w.MODE_OK, router=by_lat(w.MODE_SLOW))
    stub.slow_seconds = 5.0
    body, provider, elapsed = run.adjust(two_city, inject=True, timeout_ms=300)
    text = user_text(provider)
    check("8.1 总耗时不超预算的 1.6 倍", elapsed < 300 * 1.6, f"{elapsed:.0f}ms（预算 300ms）")
    check("8.2 快的城市照常注入", "| 成都 |" in text, "成都入表")
    check("8.3 慢的城市进'请勿编造'（不能沉默）", "请勿为它们编造天气：乐山" in text, "乐山进未覆盖")
    check("8.4 仍返回合法建议", ok_body(body, two_city), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 9 异常数据
    print("\n=== 9 上游返回异常数据（200 但字段全空）===", flush=True)
    stub.reset(mode=w.MODE_ABNORMAL)
    body, provider, _ = run.adjust(two_city, inject=True)
    text = user_text(provider)
    check("9.1 异常数据 → 无注入块", "【已核实的天气预报】" not in text, "整块不注入")
    check("9.2 绝不出现「暂无数据」", "暂无数据" not in text, "未把未核实值抄进 prompt")
    check("9.3 仍返回合法建议", ok_body(body, two_city), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 10 白名单
    print("\n=== 10 日期白名单过滤（计划只有今天与第四天）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    gap = plan_of(["成都"], dates=[TODAY.isoformat(), (TODAY + timedelta(days=3)).isoformat()])
    body, provider, _ = run.adjust(gap, inject=True)
    text = user_text(provider)
    check("10.1 查询区间覆盖四天", stub.calls and (stub.calls[0]["end"] - stub.calls[0]["start"]).days == 3,
          f"start={stub.calls[0]['start']} end={stub.calls[0]['end']}" if stub.calls else "无调用")
    check("10.2 白名单内的两天在表里",
          f"| {TODAY.isoformat()} |" in text and f"| {(TODAY + timedelta(days=3)).isoformat()} |" in text,
          "首尾两天入表")
    check("10.3 计划外的两天不在表里",
          f"| {(TODAY + timedelta(days=1)).isoformat()} |" not in text
          and f"| {(TODAY + timedelta(days=2)).isoformat()} |" not in text, "中间两天被滤掉")

    # ---------------------------------------------------------------- 11 并发
    print("\n=== 11 并发调整（同一计划两个请求）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    weather_inject.reset_cache()
    weather_circuit.reset_all()
    stub.reset_calls()

    counter = {"model": 0}

    class CountingProvider(w.EchoProvider):
        """数一次模型调用。并发场景的判据是"请求数 == 模型调用数"——
        多出来的那次就说明发生了自动重试，而 PM 明确不允许用重试掩盖冲突。"""

        async def stream_chat(self, messages, trace_id: str = "", json_mode: bool = False):
            counter["model"] += 1
            async for piece in super().stream_chat(messages, trace_id=trace_id, json_mode=json_mode):
                yield piece

    app.state.provider = CountingProvider(suggest())
    settings.weather.inject_on_adjust = True

    import threading

    results: list[dict] = []

    def fire(trace: str) -> None:
        results.append(app_adjust(client, base_plan, trace))

    threads = [threading.Thread(target=fire, args=(f"c{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check("11.1 两个并发请求都成功", all(r.get("code") == 0 for r in results),
          f"codes={[r.get('code') for r in results]}")
    check("11.2 不自动重试（请求数 == 模型调用数）", counter["model"] == len(results),
          f"模型调用 {counter['model']} 次 / 请求 {len(results)} 个")
    check("11.3 两个请求都返回了建议，互不覆盖",
          len(results) == 2 and all((r.get("data") or {}).get("ops") for r in results),
          "两份建议都在，没有静默丢一份")

    # ---------------------------------------------------------------- 12 缓存
    print("\n=== 12 短缓存命中与过期 ===", flush=True)
    stub.reset(mode=w.MODE_OK)
    body1, p1, _ = run.adjust(base_plan, inject=True, trace="k1")
    calls_first = len(stub.calls)
    body2, p2, _ = run.adjust(base_plan, inject=True, reset_cache=False, trace="k2")
    check("12.1 第二次 0 次上游调用", calls_first == 1 and len(stub.calls) == 0,
          f"首次 {calls_first} 次 → 第二次 {len(stub.calls)} 次")
    check("12.2 两次注入块逐字相同", user_text(p1) == user_text(p2), "逐字相同")
    # AC-A9 的第二个判据是链路内天气耗时，不是墙钟 —— 墙钟里混着模型与 HTTP 开销
    second_ms = probe.by_trace.get("k2", -1)
    check("12.3 第二次 weather_ms ≤ 5（AC-A9）", 0 <= second_ms <= 5,
          f"埋点 elapsedMs={second_ms}（首次 {probe.by_trace.get('k1', -1)}）")

    stub.reset(mode=w.MODE_OK)
    run.adjust(base_plan, inject=True, cache_seconds=1, trace="k3")
    time.sleep(1.2)
    _, _, _ = run.adjust(base_plan, inject=True, cache_seconds=1, reset_cache=False, trace="k4")
    check("12.4 过期后重新查了一次", len(stub.calls) == 1, f"过期后 {len(stub.calls)} 次")

    # ---------------------------------------------------------------- 13 中文数字
    print("\n=== 13 中文数字（AC-A10，按设计放行）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    for text, label in [("夜里十几度，多带件外套", "十几度"),
                        ("风三四级，别去爬山", "三四级"),
                        ("白天三十来度，避开正午", "三十来度")]:
        body, provider, _ = run.adjust(base_plan, inject=True, payload=suggest(note=text))
        check(f"13 「{label}」按设计放行（已知限制）", body.get("code") == 0,
              "未被闸门拦截 —— 这是设计记录的限制，不是漏测")

    # ---------------------------------------------------------------- 14 闸门触发率
    print("\n=== 14 闸门触发率（AC-A12，20+20+20）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    dirty = suggest(note="明天下雨，降水概率 80%，改成室内")
    clean = suggest(note="明天有雨，把爬山换成博物馆")

    def count_blocked(inject: bool, payload: dict, rounds: int = 20) -> int:
        blocked = 0
        for index in range(rounds):
            body, _, _ = run.adjust(base_plan, inject=inject, payload=payload,
                                    reset_cache=False, trace=f"g{index}")
            if body.get("code") != 0:
                blocked += 1
        return blocked

    off_blocked = count_blocked(False, dirty)
    on_blocked = count_blocked(True, dirty)
    false_positive = count_blocked(True, clean)
    check("14.1 未注入 + 违规文本：0 次拦截（闸门不启用）", off_blocked == 0, f"{off_blocked}/20")
    check("14.2 注入 + 违规文本：全部拦截（真阳）", on_blocked == 20, f"{on_blocked}/20")
    check("14.3 注入 + 干净文本：0 次误伤（假阳）", false_positive == 0, f"{false_positive}/20")

    # ---------------------------------------------------------------- 15 数据源未启用
    print("\n=== 15 数据源未启用（provider=none）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    original_provider = settings.weather.provider
    settings.weather.provider = "none"          # 等价于生产未配凭据时的状态
    try:
        body, provider, _ = run.adjust(base_plan, inject=True)
        check("15.1 未启用时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
        check("15.2 未启用时无注入块", "【已核实的天气预报】" not in user_text(provider), "无注入块")
        check("15.3 未启用时仍返回合法建议", ok_body(body, base_plan), f"code={body.get('code')}")
    finally:
        settings.weather.provider = original_provider

    # ---------------------------------------------------------------- 16 城市无坐标
    print("\n=== 16 城市无坐标（坐标表里查不到）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    unknown = plan_of(["瓦坎达"])
    body, provider, _ = run.adjust(unknown, inject=True)
    check("16.1 坐标未知时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("16.2 坐标未知时无注入块", "【已核实的天气预报】" not in user_text(provider), "无注入块")
    check("16.3 坐标未知时仍返回合法建议", ok_body(body, unknown), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 17 全在 10 天外
    print("\n=== 17 行程全在预报窗口外（今天+20 起 3 天）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    far_start = TODAY + timedelta(days=20)
    far = plan_of(["成都"], dates=[(far_start + timedelta(days=offset)).isoformat()
                                   for offset in range(3)],
                  start=far_start.isoformat())
    body, provider, _ = run.adjust(far, inject=True)
    check("17.1 越窗口时 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("17.2 越窗口时无注入块", "【已核实的天气预报】" not in user_text(provider), "无注入块")
    check("17.3 越窗口时仍返回合法建议", ok_body(body, far), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 18 跨今天
    print("\n=== 18 行程跨今天（startDate=今天-2，共 5 天）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    crossing = plan_of(
        ["成都"],
        dates=[(TODAY - timedelta(days=2)).isoformat(), (TODAY - timedelta(days=1)).isoformat(),
               TODAY.isoformat(), TOMORROW.isoformat(), (TODAY + timedelta(days=2)).isoformat()],
        start=(TODAY - timedelta(days=2)).isoformat(),
    )
    body, provider, _ = run.adjust(crossing, inject=True)
    text = user_text(provider)
    check("18.1 跨今天的行程仍然注入（这是最典型的调整场景）",
          "【已核实的天气预报】" in text, "有注入块")
    check("18.2 只有今天起的三天在表里（AC-A6）",
          all(f"| {(TODAY + timedelta(days=offset)).isoformat()} |" in text for offset in range(3))
          and all(f"| {(TODAY - timedelta(days=offset)).isoformat()} |" not in text
                  for offset in (1, 2)),
          "今天/明天/后天入表，昨天与前天不入表")
    check("18.3 查询区间从今天开始（过去的日期不出网）",
          bool(stub.calls) and stub.calls[0]["start"] == TODAY
          and stub.calls[0]["end"] == TODAY + timedelta(days=2),
          f"start={stub.calls[0]['start']} end={stub.calls[0]['end']}" if stub.calls else "无调用")
    check("18.4 仍返回合法建议", ok_body(body, crossing), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 19 每城只绑自己日期
    print("\n=== 19 两城分段（成都 2 天 + 乐山 2 天）===", flush=True)
    stub.reset(mode=w.MODE_OK)
    day1, day2 = TODAY.isoformat(), TOMORROW.isoformat()
    day3, day4 = (TODAY + timedelta(days=2)).isoformat(), (TODAY + timedelta(days=3)).isoformat()
    split = plan_of(["成都", "成都", "乐山", "乐山"], dates=[day1, day2, day3, day4],
                    start=day1)
    body, provider, _ = run.adjust(split, inject=True)
    text = user_text(provider)
    check("19.1 成都只出现在前两天的行上（AC-A7）",
          f"| {day1} | 成都 |" in text and f"| {day2} | 成都 |" in text
          and f"| {day3} | 成都 |" not in text and f"| {day4} | 成都 |" not in text,
          "成都绑定 day1–2")
    check("19.2 乐山只出现在后两天的行上（AC-A7）",
          f"| {day3} | 乐山 |" in text and f"| {day4} | 乐山 |" in text
          and f"| {day1} | 乐山 |" not in text and f"| {day2} | 乐山 |" not in text,
          "乐山绑定 day3–4")
    check("19.3 仍返回合法建议", ok_body(body, split), f"code={body.get('code')}")

    stub.reset(mode=w.MODE_OK, router=by_lat(w.MODE_500))     # 后两天那城挂掉
    body, provider, _ = run.adjust(split, inject=True)
    text = user_text(provider)
    check("19.4 一城失败不写到另一城头上（AC-A7）",
          f"| {day1} | 成都 |" in text and f"| {day2} | 成都 |" in text
          and "| 乐山 |" not in text and "请勿为它们编造天气：乐山" in text,
          "成都四天里只有自己的两天，乐山进未覆盖")

    # ---------------------------------------------------------------- 20 响应结构
    print("\n=== 20 响应结构与键集合（AC-A4）===", flush=True)
    run.responses.extend(results)                              # 并发那两份也算进比对
    success = [body for body in run.responses if body.get("code") == 0]
    baseline_keys = sorted((success[0].get("data") or {}).keys())
    deviants = [sorted((body.get("data") or {}).keys())
                for body in success
                if sorted((body.get("data") or {}).keys()) != baseline_keys]
    check("20.1 成功响应的 data 键集合与未注入基线相等", not deviants,
          f"基线 {baseline_keys}；{len(success)} 个成功响应中不一致 {len(deviants)} 个")
    # 被闸门拒绝的那批走既有 `AdjustError`（10003）响应，形状与 S2 之前就存在的
    # "建议不合规"完全同形：顶层 {code,message,data,traceId}，data 恒为 None。
    # 这里钉住的是"没有因为注入天气而多出字段"。
    rejected = [body for body in run.responses if body.get("code") != 0]
    check("20.2 被拒响应形状不变（code/message/data/traceId，data 为 None）",
          all(sorted(body.keys()) == ["code", "data", "message", "traceId"]
              and body.get("data") is None for body in rejected),
          f"{len(rejected)} 个拒绝响应，键集合 {sorted(rejected[0].keys()) if rejected else '无'}")
    leaked = [(index, hits) for index, body in enumerate(run.responses)
              for hits in [weather_keys_in(body)] if hits]
    check("20.3 递归扫描无 weather/temperature/precipitation/wind/humidity 键",
          not leaked, f"扫描 {len(run.responses)} 个响应，命中 {len(leaked)} 个")

    failed = [item for item, status, _ in RESULTS if status == "FAIL"]
    skipped = [item for item, status, _ in RESULTS if status == "SKIP"]
    print("\n" + "=" * 78, flush=True)
    print(f"共 {len(RESULTS)} 项断言：PASS {len(RESULTS) - len(failed) - len(skipped)} / "
          f"FAIL {len(failed)} / SKIP {len(skipped)}", flush=True)
    if failed:
        print("失败项：" + "、".join(failed), flush=True)
    return 1 if failed else 0


def app_adjust(client: TestClient, plan: dict, trace: str) -> dict:
    return client.post(
        "/v1/plan/adjust",
        json={"instruction": "下午太累，改到三点", "plan": plan},
        headers={"X-Trace-Id": trace},
    ).json()


if __name__ == "__main__":
    raise SystemExit(main())
