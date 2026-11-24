"""M3-3 · S4-2 调整链路延迟实测：1200ms 是总预算，不是每城预算。

    APP_ENV=dev WEATHER_PROVIDER=open-meteo \
    .venv/Scripts/python.exe scripts/measure_adjust_latency.py

## 为什么调整链路要单独测一遍

生成链路是 SSE：先发 `intent`，再查天气，天气耗时只推迟"用户看到内容"的时点。
调整链路**没有流**：`/v1/plan/adjust` 一次性返回 JSON，天气耗时 100% 叠加在用户的
白屏时间里。所以在 M3-2 上测出来的增量不能直接搬过来。

## 三组

| 组 | 条件 | 看什么 |
|---|---|---|
| A | 注入关闭 | 基线：整体耗时 ≈ 模型耗时 |
| B | 注入开启、缓存已热 | 天气≈0，整体增量应可忽略 |
| C | 注入开启、上游慢（超过预算） | 整体增量应≈预算上限，而不是城市数×预算 |

模型用替身（固定 120ms），只模拟耗时、不模拟内容 —— 这里量的是**天气给用户加了多少等待**，
不是模型本身多快。天气替身是真休眠，让 `asyncio.wait` 的超时与取消真的发生。

## 为什么每组先预热 2 次再统计

第一次请求要建连接、首次 import provider、缓存是冷的。M3-2 的 AC-9 就被这个坑过：
不预热时对照组反而更慢，差值算出来是负的。

## 指标口径

- `total_ms`：客户端从发请求到拿完响应的墙钟时间（用户真实等待）
- `weather_ms`：链路内埋点 `elapsedMs`（不是函数微基准）
- `model_ms`：`total_ms - weather_ms`，用来核对"总预算"确实只被算了一次
"""

from __future__ import annotations

import os
import statistics
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

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.tools import weather_circuit, weather_inject  # noqa: E402
from app.tools.weather_providers import open_meteo  # noqa: E402
import wx_echo as w  # noqa: E402

TODAY = date.today()
MODEL_MS = 120          # 模型替身的固定耗时
WARMUP = 2
ROUNDS = 7

RESULTS: list[str] = []


def pct(values: list[float], p: float) -> float:
    """nearest-rank 百分位：样本少的时候比插值更诚实。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(p / 100 * len(ordered) + 0.5)) - 1))
    return ordered[index]


def report(group: str, total: list[float], weather: list[float]) -> None:
    model = [t - w for t, w in zip(total, weather)]
    line = (f"{group}: total P50 {pct(total, 50):.0f} / P95 {pct(total, 95):.0f}ms | "
            f"weather P50 {pct(weather, 50):.0f} / P95 {pct(weather, 95):.0f}ms | "
            f"model P50 {pct(model, 50):.0f}ms")
    RESULTS.append(line)
    print(line, flush=True)


class SlowModel(w.EchoProvider):
    """给模型加固定耗时，让"整体 vs 天气"两段拆得开。"""

    async def stream_chat(self, messages, trace_id: str = "", json_mode: bool = False):
        await _sleep(MODEL_MS / 1000)
        async for piece in super().stream_chat(messages, trace_id=trace_id, json_mode=json_mode):
            yield piece


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def plan_view(city: str = "成都") -> dict:
    dates = [(TODAY - timedelta(days=1)).isoformat(), TODAY.isoformat(),
             (TODAY + timedelta(days=1)).isoformat()]
    return {
        "title": "成都三日", "totalDays": 3, "startDate": dates[0], "status": "IN_PROGRESS",
        "days": [
            {"dayIndex": index + 1, "date": day, "city": city, "theme": None,
             "items": [{"itemId": str(index + 1), "itemOrder": 1, "title": "宽窄巷子",
                        "startTime": "09:00", "endTime": "11:00", "note": None}]}
            for index, day in enumerate(dates)
        ],
    }


def payload() -> dict:
    return {"summary": "把第一项挪到下午",
            "ops": [{"op": "UPDATE_ITEM", "itemId": "2",
                     "changes": [{"field": "startTime", "after": "15:00"}]}]}


def main() -> int:
    client = TestClient(app)
    stub = w.WeatherStub()
    open_meteo.fetch = stub
    collector = w.LogCollector()
    import logging

    logging.getLogger("ai-travel.weather").addHandler(collector)
    logging.getLogger("ai-travel.weather").setLevel(logging.INFO)

    app.state.provider = SlowModel(payload())
    plan = plan_view()

    def once(inject: bool, *, warm: bool, timeout_ms: int | None = None) -> tuple[float, float, dict]:
        settings.weather.inject_on_adjust = inject
        if timeout_ms is not None:
            settings.weather.inject_adjust_timeout_ms = timeout_ms
        weather_circuit.reset_all()      # 熔断开着会让 C 组"不进上游"，测出来是假的
        collector.clear()
        started = time.perf_counter()
        body = client.post("/v1/plan/adjust",
                           json={"instruction": "下午太累，改到三点", "plan": plan},
                           headers={"X-Trace-Id": "lat"}).json()
        total = (time.perf_counter() - started) * 1000
        weather = float(collector.elapsed_ms() or 0)
        if not warm:
            weather_inject.reset_cache()
        return total, weather, body

    def group(inject: bool, *, slow: bool, timeout_ms: int | None = None,
              reset_each: bool = False) -> tuple[list[float], list[float], list[int]]:
        stub.reset(mode=w.MODE_SLOW if slow else w.MODE_OK)
        stub.slow_seconds = 5.0
        totals: list[float] = []
        weathers: list[float] = []
        codes: list[int] = []
        weather_inject.reset_cache()
        for index in range(WARMUP + ROUNDS):
            if reset_each:
                weather_inject.reset_cache()
            total, weather, body = once(inject, warm=not reset_each, timeout_ms=timeout_ms)
            codes.append(body.get("code", -1))
            if index < WARMUP:
                continue
            totals.append(total)
            weathers.append(weather)
        return totals, weathers, codes

    # ---------------------------------------------------------------- A 关闭
    print("=== A 注入关闭（基线）===", flush=True)
    a_total, a_weather, a_codes = group(False, slow=False, reset_each=True)
    report("A 关闭", a_total, a_weather)

    # ---------------------------------------------------------------- B 热缓存
    print("\n=== B 注入开启（缓存已热）===", flush=True)
    b_total, b_weather, b_codes = group(True, slow=False, reset_each=False)
    report("B 缓存热", b_total, b_weather)

    # ---------------------------------------------------------------- C 超时
    print("\n=== C 注入开启（上游慢，默认 1200ms 预算）===", flush=True)
    c_total, c_weather, c_codes = group(True, slow=True, reset_each=True)
    report("C 上游超时", c_total, c_weather)

    # ---------------------------------------------------------------- 判据
    print("\n" + "=" * 78, flush=True)
    checks: list[tuple[str, bool, str]] = []

    checks.append(("A 组全部成功", all(code == 0 for code in a_codes), f"codes 去重={set(a_codes)}"))
    checks.append(("B 组全部成功", all(code == 0 for code in b_codes), f"codes 去重={set(b_codes)}"))
    checks.append(("C 组全部成功（超时也要出建议）", all(code == 0 for code in c_codes),
                   f"codes 去重={set(c_codes)}"))

    checks.append(("测量有效：模型耗时可见（total ≥ 100ms）", pct(a_total, 50) >= 100,
                   f"A 组 total P50 {pct(a_total, 50):.0f}ms，模型替身 {MODEL_MS}ms"))

    delta_b = pct(b_total, 50) - pct(a_total, 50)
    checks.append(("B 组增量 ≤ 50ms（缓存命中时天气几乎不占时间）", delta_b <= 50,
                   f"增量 {delta_b:+.0f}ms；天气 P95 {pct(b_weather, 95):.0f}ms"))

    budget = settings.weather.inject_adjust_timeout_ms
    delta_c = pct(c_total, 50) - pct(a_total, 50)
    checks.append((f"C 组增量 ≈ 预算 {budget}ms（±20%）",
                   abs(delta_c - budget) <= budget * 0.2,
                   f"增量 {delta_c:+.0f}ms"))
    checks.append(("C 组天气耗时不超过预算", pct(c_weather, 95) <= budget * 1.2,
                   f"weather P95 {pct(c_weather, 95):.0f}ms / 预算 {budget}ms"))

    for item, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        RESULTS.append(f"[{status}] {item} —— {detail}")
        print(f"[{status}] {item} —— {detail}", flush=True)

    failed = [line for line in RESULTS if line.startswith("[FAIL]")]
    print("\n" + "=" * 78, flush=True)
    print(f"共 {len(checks)} 项判据：PASS {len(checks) - len(failed)} / FAIL {len(failed)} / SKIP 0",
          flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
