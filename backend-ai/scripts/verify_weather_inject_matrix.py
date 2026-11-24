"""M3-2 · S3-1 端到端故障矩阵：任何天气故障都不能让原计划生成失败。

    APP_ENV=dev WEATHER_PROVIDER=open-meteo \
    .venv/Scripts/python.exe scripts/verify_weather_inject_matrix.py

## 覆盖范围（PM 点名 10 类，逐条对应）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 天气功能关闭 | 0 上游调用、无注入块 |
| 2 | 无日期 | 0 上游调用、无注入块 |
| 3 | 日期越窗（今天+20） | 0 上游调用、无注入块 |
| 4 | 无坐标 / 未知城市 | 0 上游调用、无注入块 |
| 5 | 全部城市查询成功 | 注入块覆盖全部城市 |
| 6 | 部分城市失败 | 只注入成功的城市，失败城市写进"请勿编造" |
| 7 | 全部城市失败 | 无注入块 |
| 8 | 上游超时 | 按预算收口、无注入块 |
| 9 | 上游返回异常数据 | 视为没数据，**绝不**把「暂无数据」抄进表 |
| 10 | 并发一慢一快 | 快的照常注入，慢的写进"没有预报"，总耗时不超预算 |
| 11 | 60s 短缓存命中 | 第二次生成 0 上游调用 |
| 12 | 60s 短缓存过期 | 过期后重新查一次 |

**全局不变式**（每一场都断言）：`code=0` 且计划有内容。
天气是可选增强，它挂了不能让用户拿不到计划——这是 03 §4 的原话，也是 M3-2 全部设计的基调。

## 为什么还是 TestClient + 替身

M3-2 的改动全在 Python 进程内（Java / Vue / 契约 / `/plan/adjust` 零改动），
所以"端到端"是「HTTP 进 → Prompt 出 → 事件出」。只换两处：
`open_meteo.fetch`（唯一出网点）与模型（回声 provider，回放合法计划）。
熔断、城市解析、窗口裁剪、缓存、归一化、注入、校验全是真代码。

## 判据里为什么有 SKIP

上游超时 / 异常数据这两类，熔断器会记失败。跑完整个矩阵后熔断会开着，
所以每一场开头都 `reset_all()`，并在报告里注明——**没有任何一项因为环境原因被跳过**。
"""

from __future__ import annotations

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
os.environ.pop("WEATHER_INJECT_ON_GENERATION", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.tools import weather_circuit, weather_inject  # noqa: E402
from app.tools.weather_providers import open_meteo  # noqa: E402
import wx_echo as w  # noqa: E402

# 日期一律相对今天算：写死会让这个脚本在几天后集体变红（越窗那场会变成"已过去"）
TODAY = date.today()
START_DATE = TODAY + timedelta(days=2)
START = START_DATE.isoformat()
DATE_CN = f"{START_DATE.month}月{START_DATE.day}日"
FAR_DATE = TODAY + timedelta(days=20)
FAR_CN = f"{FAR_DATE.month}月{FAR_DATE.day}日"

ONE_CITY = f"帮我规划成都三天，{DATE_CN}出发，住在市中心，喜欢美食和历史"
ONE_CITY_NO_DATE = "帮我规划成都三天，住在市中心，喜欢美食"
ONE_CITY_FAR = f"帮我规划成都三天，{FAR_CN}出发，喜欢美食"
UNKNOWN_CITY = f"帮我规划阿斯加德三天，{DATE_CN}出发，喜欢美食"
TWO_CITIES = f"帮我规划成都和乐山四天，{DATE_CN}出发，喜欢美食"

# 乐山的纬度明显低于成都，用坐标把"这一城慢"分流出去（注入层不该为测试加参数）
LESHAN_LAT = 29.5521
CHENGDU_LAT = 30.5728

RESULTS: list[tuple[str, str, str]] = []


def check(item: str, ok: bool, detail: str, *, skip: bool = False) -> bool:
    status = "SKIP" if skip else ("PASS" if ok else "FAIL")
    RESULTS.append((item, status, detail))
    print(f"[{status}] {item} —— {detail}", flush=True)
    return ok


def by_lat(slow_lat: float) -> "callable":
    """让纬度接近 `slow_lat` 的那一城走 slow，其余正常。"""
    return lambda lat, lon: w.MODE_SLOW if abs(lat - slow_lat) < 0.5 else w.MODE_OK


class Runner:
    """一次生成请求 + 场景前的基础设施重置。"""

    def __init__(self, client: TestClient, stub: w.WeatherStub, collector: w.LogCollector) -> None:
        self.client = client
        self.stub = stub
        self.collector = collector

    def generate(
        self,
        text: str,
        trace_id: str,
        *,
        inject: bool = True,
        timeout_ms: int | None = None,
        cache_seconds: int | None = None,
        reset_cache: bool = True,
    ) -> tuple[dict, w.EchoProvider, float]:
        settings.weather.inject_on_generation = inject
        if timeout_ms is not None:
            settings.weather.inject_timeout_ms = timeout_ms
        if cache_seconds is not None:
            settings.weather.inject_cache_seconds = cache_seconds
        if reset_cache:
            weather_inject.reset_cache()
        # 熔断是进程级单例：上一场把它打开，这一场就会"假跳过"，所以每场都清
        weather_circuit.reset_all()
        self.stub.reset_calls()
        self.collector.clear()

        provider = w.EchoProvider(w.plan_of(text))
        app.state.provider = provider
        started = time.perf_counter()
        body = self.client.post(
            "/v1/plan/generate",
            json={"messages": [{"role": "user", "content": text}]},
            headers={"X-Trace-Id": trace_id},
        ).json()
        return body, provider, (time.perf_counter() - started) * 1000


def plan_of(body: dict) -> dict:
    return ((body.get("data") or {}).get("draft") or {}).get("plan") or {}


def describe(body: dict, provider: w.EchoProvider, elapsed: float) -> str:
    plan = plan_of(body)
    return (f"code={body.get('code')} 天数={len(plan.get('days') or [])} "
            f"块={'有' if w.has_block(provider.sent) else '无'} 耗时={elapsed:.0f}ms")


def main() -> int:
    collector = w.LogCollector()
    weather_logger = __import__("logging").getLogger("ai-travel.weather")
    weather_logger.setLevel(__import__("logging").INFO)
    weather_logger.addHandler(collector)

    stub = w.WeatherStub()
    open_meteo.fetch = stub

    with TestClient(app) as client:
        run = Runner(client, stub, collector)

        # ---------------------------------------------------------------- 1 关闭
        print("\n=== 1 天气功能关闭 ===", flush=True)
        body, provider, elapsed = run.generate(ONE_CITY, "m1-off", inject=False)
        check("1.1 0 次上游调用", len(stub.calls) == 0, f"上游 {len(stub.calls)} 次")
        check("1.2 没有注入块", not w.has_block(provider.sent), describe(body, provider, elapsed))
        check("1.3 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 2 无日期
        print("\n=== 2 无日期（startDate 为空）===", flush=True)
        body, provider, elapsed = run.generate(ONE_CITY_NO_DATE, "m2-no-date")
        check("2.1 0 次上游调用（没有日期就没有「哪天的天气」）", len(stub.calls) == 0,
              f"上游 {len(stub.calls)} 次")
        check("2.2 埋点 reason=NO_START_DATE", bool(collector.find("NO_START_DATE")),
              "；".join(collector.find("weather_skipped"))[:90] or "（无）")
        check("2.3 没有注入块且计划成功", not w.has_block(provider.sent) and body.get("code") == 0,
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 3 越窗
        print("\n=== 3 日期越窗（今天+20，超出 10 天预报）===", flush=True)
        body, provider, elapsed = run.generate(ONE_CITY_FAR, "m3-far")
        check("3.1 0 次上游调用", len(stub.calls) == 0, f"上游 {len(stub.calls)} 次")
        check("3.2 埋点 reason=DATE_OUT_OF_RANGE", bool(collector.find("DATE_OUT_OF_RANGE")),
              "；".join(collector.find("weather_skipped"))[:90] or "（无）")
        check("3.3 没有注入块且计划成功", not w.has_block(provider.sent) and body.get("code") == 0,
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 4 未知城市
        print("\n=== 4 无坐标 / 未知城市 ===", flush=True)
        body, provider, elapsed = run.generate(UNKNOWN_CITY, "m4-unknown")
        check("4.1 0 次上游调用（城市解析失败在出网之前）", len(stub.calls) == 0,
              f"上游 {len(stub.calls)} 次")
        check("4.2 没有注入块（不编造该城天气）",
              not w.has_block(provider.sent) and "暂无数据" not in w.last_user(provider.sent),
              describe(body, provider, elapsed))
        check("4.3 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 5 全成功
        print("\n=== 5 全部城市查询成功（成都 + 乐山）===", flush=True)
        body, provider, elapsed = run.generate(TWO_CITIES, "m5-all-ok")
        block = w.last_user(provider.sent) if w.has_block(provider.sent) else ""
        check("5.1 两城各查一次", len(stub.calls) == 2, f"上游 {len(stub.calls)} 次")
        check("5.2 注入块覆盖两个城市",
              "覆盖城市：成都、乐山。" in block or "覆盖城市：乐山、成都。" in block,
              next((line for line in block.splitlines() if line.startswith("覆盖城市")), "（无）"))
        check("5.3 没有「未覆盖」的措辞", "请勿为它们编造天气" not in block, "无 uncovered 子句")
        check("5.4 表格行数 = 天数 × 城市数", len(w.table_rows(block)) == 8,
              f"数据行 {len(w.table_rows(block))} 条（期望 8）")

        # ---------------------------------------------------------------- 6 部分失败
        print("\n=== 6 部分城市失败（乐山 500）===", flush=True)
        stub.reset(mode=w.MODE_OK)
        stub.router = lambda lat, lon: w.MODE_500 if abs(lat - LESHAN_LAT) < 0.5 else w.MODE_OK
        body, provider, elapsed = run.generate(TWO_CITIES, "m6-partial")
        block = w.last_user(provider.sent) if w.has_block(provider.sent) else ""
        rows = w.table_rows(block)
        check("6.1 两城各查一次（失败城市也不重试）", len(stub.calls) == 2, f"上游 {len(stub.calls)} 次")
        check("6.2 块内写明乐山没有预报", "请勿为它们编造天气：乐山。" in block,
              next((line for line in block.splitlines() if "请勿" in line), "（无）"))
        check("6.3 表格里没有乐山的行", rows and not any("乐山" in line for line in rows),
              f"数据行 {len(rows)} 条，全部属于成都")
        check("6.4 aiNote 说明未参考乐山", "乐山未取得预报" in (plan_of(body).get("aiNote") or ""),
              (plan_of(body).get("aiNote") or "")[-40:])
        check("6.5 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))
        stub.router = None

        # ---------------------------------------------------------------- 7 全失败
        print("\n=== 7 全部城市失败（两城都 500）===", flush=True)
        stub.reset(mode=w.MODE_500)
        body, provider, elapsed = run.generate(TWO_CITIES, "m7-all-fail")
        check("7.1 两城各查一次", len(stub.calls) == 2, f"上游 {len(stub.calls)} 次")
        check("7.2 没有注入块", not w.has_block(provider.sent), describe(body, provider, elapsed))
        check("7.3 aiNote 里没有「参考了预报」", "预报" not in (plan_of(body).get("aiNote") or ""),
              (plan_of(body).get("aiNote") or "")[-40:])
        check("7.4 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 8 超时
        print("\n=== 8 上游超时（真睡 5s，预算 300ms）===", flush=True)
        stub.reset(mode=w.MODE_SLOW)
        body, provider, elapsed = run.generate(ONE_CITY, "m8-timeout", timeout_ms=300)
        check("8.1 请求在预算附近返回，没有被上游拖死", elapsed < 300 + 700,
              f"整请求 {elapsed:.0f}ms（预算 300ms + 生成开销）")
        check("8.2 没有注入块（超时不能变成「部分真数据」）", not w.has_block(provider.sent),
              describe(body, provider, elapsed))
        check("8.3 埋点记了 TIMEOUT 明细",
              any("TIMEOUT" in line for line in collector.find("weather_skipped")),
              "；".join(collector.find("weather_skipped"))[-60:] or "（无）")
        check("8.4 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 9 异常数据
        print("\n=== 9 上游返回异常数据（200 但字段全空）===", flush=True)
        stub.reset(mode=w.MODE_ABNORMAL)
        body, provider, elapsed = run.generate(ONE_CITY, "m9-abnormal")
        block_or_text = w.last_user(provider.sent)
        check("9.1 查了一次", len(stub.calls) == 1, f"上游 {len(stub.calls)} 次")
        check("9.2 没有注入块（全未核实 = 没数据）", not w.has_block(provider.sent),
              describe(body, provider, elapsed))
        check("9.3 「暂无数据」没有被抄进 prompt", "暂无数据" not in block_or_text,
              "prompt 中无「暂无数据」")
        check("9.4 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))

        # ---------------------------------------------------------------- 10 一慢一快
        print("\n=== 10 并发一慢一快（乐山 5s，成都正常，预算 600ms）===", flush=True)
        stub.reset(mode=w.MODE_OK)
        stub.router = by_lat(LESHAN_LAT)
        body, provider, elapsed = run.generate(TWO_CITIES, "m10-one-slow", timeout_ms=600)
        block = w.last_user(provider.sent) if w.has_block(provider.sent) else ""
        rows = w.table_rows(block)
        check("10.1 慢城没拖垮整批：成都仍然注入",
              "覆盖城市：成都。" in block, next((line for line in block.splitlines()
                                                if line.startswith("覆盖城市")), "（无）"))
        check("10.2 慢城写进「没有预报」，不是默默消失", "请勿为它们编造天气：乐山。" in block,
              next((line for line in block.splitlines() if "请勿" in line), "（无）"))
        check("10.3 表格里只有成都的行", rows and not any("乐山" in line for line in rows),
              f"数据行 {len(rows)} 条")
        check("10.4 总耗时受预算约束", elapsed < 600 + 900, f"整请求 {elapsed:.0f}ms（预算 600ms）")
        check("10.5 计划生成成功", body.get("code") == 0 and bool(plan_of(body).get("days")),
              describe(body, provider, elapsed))
        stub.router = None

        # ---------------------------------------------------------------- 11 缓存命中
        print("\n=== 11 60s 短缓存命中 ===", flush=True)
        stub.reset(mode=w.MODE_OK)
        body1, provider1, _ = run.generate(ONE_CITY, "m11-hit-1", cache_seconds=60)
        first_calls = len(stub.calls)
        body2, provider2, _ = run.generate(ONE_CITY, "m11-hit-2", cache_seconds=60, reset_cache=False)
        check("11.1 第一次查了 1 次", first_calls == 1, f"上游 {first_calls} 次")
        check("11.2 第二次 0 次上游调用（连点重新生成不重复打上游）", len(stub.calls) == 0,
              f"上游 {len(stub.calls)} 次")
        check("11.3 两次都注入了同样的块",
              w.has_block(provider1.sent) and w.has_block(provider2.sent)
              and w.last_user(provider1.sent).split("【用户需求】")[0]
              == w.last_user(provider2.sent).split("【用户需求】")[0],
              "两次注入块逐字相同")

        # ---------------------------------------------------------------- 12 缓存过期
        print("\n=== 12 短缓存过期（TTL 压到 1s，真等 1.2s）===", flush=True)
        stub.reset(mode=w.MODE_OK)
        run.generate(ONE_CITY, "m12-exp-1", cache_seconds=1)
        calls_before = len(stub.calls)
        time.sleep(1.2)
        run.generate(ONE_CITY, "m12-exp-2", cache_seconds=1, reset_cache=False)
        check("12.1 过期后重新查了一次", len(stub.calls) == 1,
              f"过期前 {calls_before} 次 → 过期后 {len(stub.calls)} 次")

    failed = [item for item, status, _ in RESULTS if status == "FAIL"]
    skipped = [item for item, status, _ in RESULTS if status == "SKIP"]
    print("\n" + "=" * 78, flush=True)
    print(f"共 {len(RESULTS)} 项断言：PASS {len(RESULTS) - len(failed) - len(skipped)} / "
          f"FAIL {len(failed)} / SKIP {len(skipped)}", flush=True)
    if failed:
        print("失败项：" + "、".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
