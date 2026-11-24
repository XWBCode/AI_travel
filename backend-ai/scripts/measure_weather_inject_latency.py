"""M3-2 · S3-2 AC-9 首字延迟实测（走真实 `/plan/stream` 链路）。

    APP_ENV=dev WEATHER_PROVIDER=open-meteo \
    .venv/Scripts/python.exe scripts/measure_weather_inject_latency.py

## 测的是什么（这里有个容易搞错的点）

注入发生在**第一次 LLM token 之前**，但 `run_generation` 是**先 yield `intent` 再做注入**。
所以注入推迟的不是"第一个 SSE 事件"，而是"计划内容的第一个字节"。
如果只测"首字节"，会得出"注入不影响首字延迟"这个错误结论——用户明明多等了两秒。

因此这里同时记三个时刻，并用埋点里的 `elapsedMs` 作为链路内的天气耗时：

| 指标 | 含义 | 谁在意 |
|---|---|---|
| `t_intent` | 第一个 SSE 事件（客户端知道"开始了"） | 连接是否立刻建立 |
| `t_draft` | `plan_draft` 事件（用户**看到行程内容**） | 真正的首字延迟 |
| `t_done` | 整请求结束 | 总耗时 |
| `weather` | 注入阶段耗时（埋点实测） | 归因：增量是不是天气造成的 |

## 三组（同输入、同 provider、同进程、同 loopback）

- **A 对照组**：注入关闭
- **B 实验组**：注入开启、上游正常
- **C 超时组**：注入开启、上游真睡 5s（预算 2s）

## 判据

AC-9：`t_draft` 的 **P95 增量 ≤ 2500ms**（预算 2s + 生成与序列化开销）。
B、C 两组都要满足——C 组的增量就是"最坏情况"。

## 关于"模型"

模型用回声 provider（回放一份合法计划），A/B/C 三组用的是**同一个**，
所以模型与网络这两个变量被固定住了，测出来的差值只可能是注入造成的。
代价是：这个数字**不能**当成"真实模型下的首字延迟"，它是**注入带来的增量**。
真实模型的首字延迟取决于上游（DeepSeek 等），与本次改动无关。

## 为什么起一个真 uvicorn 而不是 TestClient

第一版用 TestClient 的 `stream()`，结果三组的 `t_intent`、`t_draft`、`t_done` 三个数字
**一模一样**（差 0.1ms），也就是整段 SSE 被攒成一块才交过来——那样测出来的根本不是首字延迟。
于是改成"在本机起一个真 uvicorn（临时端口）+ httpx 真 HTTP 流式读"。
另外加了一条"测量本身有效"的自检（AC-9.6）：超时组的 `t_draft - t_intent` 必须接近 2s，
否则说明又攒包了，那组数字不可信。
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["APP_ENV"] = "dev"
os.environ["WEATHER_PROVIDER"] = "open-meteo"
os.environ["LLM_PROVIDER"] = "mock"
os.environ.pop("WEATHER_INJECT_ON_GENERATION", None)

from fastapi.testclient import TestClient  # noqa: E402,F401  —— 仅历史注释引用，不再使用

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.tools import weather_circuit, weather_inject  # noqa: E402
from app.tools.weather_providers import open_meteo  # noqa: E402
import wx_echo as w  # noqa: E402

START_DATE = date.today() + timedelta(days=2)
DATE_CN = f"{START_DATE.month}月{START_DATE.day}日"
TEXT = f"帮我规划成都三天，{DATE_CN}出发，住在市中心，喜欢美食和历史"

# 重复次数：A/B 组每次只要几十毫秒，C 组每次固定 2s（预算），所以少跑几次控制总时长
REPEATS = {"A": 15, "B": 15, "C": 10}
TIMEOUT_BUDGET_MS = 2000
AC9_LIMIT_MS = 2500

RESULTS: list[tuple[str, str, str]] = []


def check(item: str, ok: bool, detail: str) -> bool:
    status = "PASS" if ok else "FAIL"
    RESULTS.append((item, status, detail))
    print(f"[{status}] {item} —— {detail}", flush=True)
    return ok


def pct(values: list[float], p: float) -> float:
    """nearest-rank 百分位。样本少的时候比线性插值老实：不会造出一个没出现过的数。"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[index]


class Sample:
    def __init__(self, t_intent: float, t_draft: float, t_done: float, weather: float) -> None:
        self.t_intent = t_intent
        self.t_draft = t_draft
        self.t_done = t_done
        self.weather = weather


class LocalServer:
    """本机临时端口的真 uvicorn：SSE 必须走真 HTTP 才能观察到逐条到达。"""

    def __init__(self, asgi_app) -> None:
        config = uvicorn.Config(asgi_app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = 0

    def __enter__(self) -> "LocalServer":
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline and not self.server.started:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("本地服务未能在 15s 内启动")
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        print(f"\n本地服务已启动：http://127.0.0.1:{self.port}（真 HTTP，SSE 逐条到达）", flush=True)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def measure_once(http: httpx.Client, url: str,
                 stub: w.WeatherStub, collector: w.LogCollector,
                 *, inject: bool, trace_id: str) -> Sample:
    settings.weather.inject_on_generation = inject
    settings.weather.inject_timeout_ms = TIMEOUT_BUDGET_MS
    weather_inject.reset_cache()
    weather_circuit.reset_all()   # 熔断是进程级单例，不清会串场次
    stub.reset_calls()
    collector.clear()

    provider = w.EchoProvider(w.plan_of(TEXT))
    app.state.provider = provider
    payload = {"messages": [{"role": "user", "content": TEXT}]}

    t_intent = t_draft = t_done = None
    started = time.perf_counter()
    with http.stream("POST", url, json=payload, headers={"X-Trace-Id": trace_id}) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            now = (time.perf_counter() - started) * 1000
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
                if name == "intent" and t_intent is None:
                    t_intent = now
                elif name == "plan_draft" and t_draft is None:
                    t_draft = now
                elif name == "done":
                    t_done = now
                    break
    if t_done is None:
        t_done = (time.perf_counter() - started) * 1000
    if t_intent is None or t_draft is None:
        raise RuntimeError(f"事件缺失 trace={trace_id} intent={t_intent} draft={t_draft}")

    weather = collector.elapsed_ms()
    return Sample(t_intent, t_draft, t_done, float(weather if weather is not None else -1))


def run_scene(http: httpx.Client, url: str, stub: w.WeatherStub, collector: w.LogCollector,
              name: str, *, inject: bool, mode: str) -> list[Sample]:
    stub.reset(mode=mode)
    samples: list[Sample] = []
    count = REPEATS[name]
    print(f"\n=== {name} 组：inject={inject} 上游={mode}，重复 {count} 次 ===", flush=True)
    # 预热 2 次并丢弃：第一次请求要建连接、首次 import/编译也有开销，
    # 把它们算进 P95 会让"对照组"看起来更慢，差值就变成负数（第一版就踩了）
    for index in range(2):
        measure_once(http, url, stub, collector, inject=inject, trace_id=f"{name}-warm-{index}")
    print("  预热 2 次（不计入样本）", flush=True)

    for index in range(count):
        sample = measure_once(http, url, stub, collector, inject=inject,
                              trace_id=f"{name}-{index}")
        samples.append(sample)
        print(f"  #{index + 1:02d} intent={sample.t_intent:7.1f}ms draft={sample.t_draft:7.1f}ms "
              f"done={sample.t_done:7.1f}ms weather={sample.weather:7.1f}ms", flush=True)
    return samples


def summarize(name: str, samples: list[Sample]) -> dict[str, float]:
    intent = [s.t_intent for s in samples]
    draft = [s.t_draft for s in samples]
    done = [s.t_done for s in samples]
    weather = [max(s.weather, 0.0) for s in samples]
    row = {
        "intent_p50": pct(intent, 50), "intent_p95": pct(intent, 95),
        "draft_p50": pct(draft, 50), "draft_p95": pct(draft, 95),
        "done_p50": pct(done, 50), "done_p95": pct(done, 95),
        "weather_p50": pct(weather, 50), "weather_p95": pct(weather, 95),
    }
    print(f"\n{name} 组汇总（n={len(samples)}）", flush=True)
    print(f"  t_intent  P50={row['intent_p50']:7.1f}  P95={row['intent_p95']:7.1f}", flush=True)
    print(f"  t_draft   P50={row['draft_p50']:7.1f}  P95={row['draft_p95']:7.1f}   ← AC-9 看这个", flush=True)
    print(f"  t_done    P50={row['done_p50']:7.1f}  P95={row['done_p95']:7.1f}", flush=True)
    print(f"  weather   P50={row['weather_p50']:7.1f}  P95={row['weather_p95']:7.1f}", flush=True)
    return row


def main() -> int:
    collector = w.LogCollector()
    weather_logger = logging.getLogger("ai-travel.weather")
    weather_logger.setLevel(logging.INFO)
    weather_logger.addHandler(collector)

    stub = w.WeatherStub()
    stub.slow_seconds = 5.0
    open_meteo.fetch = stub

    with LocalServer(app) as server:
        url = f"http://127.0.0.1:{server.port}/v1/plan/stream"
        # 三组共用一个 http 客户端：连接复用、keep-alive 保持一致，避免"每组重新握手"污染增量
        with httpx.Client(timeout=30.0) as http:
            a_samples = run_scene(http, url, stub, collector, "A", inject=False, mode=w.MODE_OK)
            b_samples = run_scene(http, url, stub, collector, "B", inject=True, mode=w.MODE_OK)
            c_samples = run_scene(http, url, stub, collector, "C", inject=True, mode=w.MODE_SLOW)
            a = summarize("A", a_samples)
            b = summarize("B", b_samples)
            c = summarize("C", c_samples)

    print("\n" + "=" * 78, flush=True)
    print("增量（相对对照组的 t_draft，即用户看到行程内容的时点）", flush=True)
    delta_b = b["draft_p95"] - a["draft_p95"]
    delta_c = c["draft_p95"] - a["draft_p95"]
    delta_intent_b = b["intent_p95"] - a["intent_p95"]
    print(f"  B 组 P95 增量 = {delta_b:7.1f}ms（{b['draft_p95']:.1f} - {a['draft_p95']:.1f}）", flush=True)
    print(f"  C 组 P95 增量 = {delta_c:7.1f}ms（{c['draft_p95']:.1f} - {a['draft_p95']:.1f}）", flush=True)
    print(f"  B 组 t_intent P95 增量 = {delta_intent_b:7.1f}ms（第一个事件不该被推迟）", flush=True)

    print("\n判据", flush=True)
    check("AC-9.1 实验组（上游正常）首字延迟 P95 增量 ≤ 2500ms", delta_b <= AC9_LIMIT_MS,
          f"{delta_b:.1f}ms")
    check("AC-9.2 超时组（上游 5s）首字延迟 P95 增量 ≤ 2500ms", delta_c <= AC9_LIMIT_MS,
          f"{delta_c:.1f}ms")
    check("AC-9.3 超时组的天气耗时有上界（预算 2000ms + 余量）",
          c["weather_p95"] <= TIMEOUT_BUDGET_MS + 300,
          f"weather P95 = {c['weather_p95']:.1f}ms")
    check("AC-9.4 注入不推迟第一个 SSE 事件（intent 先于注入发出）",
          delta_intent_b <= 200, f"t_intent P95 增量 = {delta_intent_b:.1f}ms")

    # 测量本身是否有效：SSE 要真逐条到达，注入要真的卡在 intent 之后。
    # 若又攒包，C 组的 draft-intent 会接近 0，那三组数字全都不能用。
    gap_c = pct([s.t_draft - s.t_intent for s in c_samples], 50)
    gap_a = pct([s.t_draft - s.t_intent for s in a_samples], 50)
    check("AC-9.6 测量有效：C 组 draft 比 intent 晚 ≈ 预算（说明 SSE 逐条到达）",
          gap_c >= TIMEOUT_BUDGET_MS * 0.9,
          f"C 组 draft-intent P50 = {gap_c:.1f}ms；A 组同口径 = {gap_a:.1f}ms")
    check("AC-9.7 上游正常时注入开销可忽略（天气耗时 P95 ≤ 100ms）",
          b["weather_p95"] <= 100, f"B 组 weather P95 = {b['weather_p95']:.1f}ms")
    check("AC-9.5 三组都 100% 出计划", True,
          f"A/B/C 各 {len(a_samples)}/{len(b_samples)}/{len(c_samples)} 次均有 plan_draft")

    failed = [item for item, status, _ in RESULTS if status == "FAIL"]
    print("\n" + "=" * 78, flush=True)
    print(f"共 {len(RESULTS)} 项判据：PASS {len(RESULTS) - len(failed)} / FAIL {len(failed)}", flush=True)
    if failed:
        print("失败项：" + "、".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
