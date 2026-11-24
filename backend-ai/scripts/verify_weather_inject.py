"""M3-2 · S2 端到端验证：天气真的进了最终 Prompt，且失败时生成链路照常出计划。

    APP_ENV=dev WEATHER_PROVIDER=open-meteo \
    .venv/Scripts/python.exe scripts/verify_weather_inject.py

## 为什么不需要起 Java / 前端

M3-2 的全部改动落在 **Python 进程内部**（Java、Vue、计划契约、`/plan/adjust` 零改动），
所以"端到端"在这里就是「HTTP 进 → 最终 Prompt 出 → 计划事件出」。
用 FastAPI 的 TestClient 真跑一遍 `POST /v1/plan/generate`，只在两处做替身：

| 替身 | 位置 | 上面一层是真的 |
|---|---|---|
| 天气上游 | `open_meteo.fetch`（唯一出网点） | `fetch_forecast` 的城市解析、窗口裁剪、熔断、归一化全是真的 |
| 模型 | 回声 provider（落盘 messages，回放合法计划） | `run_generation` 的注入、校验、事件序列全是真的 |

回声 provider 是这一环唯一不可替代的东西：单测能证明"我们拼了一段字符串"，
证明不了"模型那边收到的最终 prompt 长什么样"。

## 六条要证明的事

A 默认关闭：0 次上游调用，prompt 与基线逐字一致
B 注入生效：块进了 prompt，`aiNote` 说明来源，计划里**没有**天气字段
C 坐标未知的城市：不发请求，且被明确写进"请勿编造"
D 上游 500：生成照常成功、无注入、不重试
E 熔断后：连续生成不再打上游，生成仍然成功
F 埋点：`weather_injected` / `weather_skipped` 两条日志都出现，且 reason 可分辨
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在导入 app 之前设好：配置在 import 时加载，天气数据源的合规闸门也在那一步
os.environ["APP_ENV"] = "dev"                 # open-meteo 只允许在 dev/test（weather_policy）
os.environ["WEATHER_PROVIDER"] = "open-meteo"
os.environ["LLM_PROVIDER"] = "mock"           # 只为让 app 起得来，生成时会换成回声 provider
os.environ.pop("WEATHER_INJECT_ON_GENERATION", None)   # 不配 = 关，这是 A 场景的前提

from fastapi.testclient import TestClient  # noqa: E402

from app.config import WeatherSettings, settings  # noqa: E402
from app.llm.prompts import build_plan_messages  # noqa: E402
from app.llm.provider import LLMProvider  # noqa: E402
from app.main import app  # noqa: E402
from app.plan import mock  # noqa: E402
from app.plan.intent import analyze_messages  # noqa: E402
from app.tools import weather_circuit, weather_inject  # noqa: E402
from app.tools.weather_providers import open_meteo  # noqa: E402
from app.tools.weather_providers.base import (  # noqa: E402
    NormalizedDay,
    ProviderFailure,
    ProviderResult,
)

# 出发日必须在 10 天预报窗口内。日期写死会让这个脚本在几天后集体变红
START_DATE = date.today() + timedelta(days=2)
START = START_DATE.isoformat()
DATE_CN = f"{START_DATE.month}月{START_DATE.day}日"
SINGLE = f"帮我规划成都三天，{DATE_CN}出发，住在市中心，喜欢美食和历史"
MULTI = f"帮我规划成都和阿斯加德四天，{DATE_CN}出发，喜欢美食"
UNKNOWN_CITY = "阿斯加德"   # 坐标表里没有的城市（测试用，别换成真实城市）

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} —— {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- 替身


class WeatherStub:
    """`open_meteo.fetch` 的替身：唯一出网点，按 mode 返回真数据或 500。"""

    def __init__(self) -> None:
        self.calls: list[tuple[date, date]] = []
        self.mode = "ok"      # ok | http_500

    async def __call__(self, *, lat, lon, start, end, **kwargs) -> ProviderResult:
        self.calls.append((start, end))
        if self.mode == "http_500":
            raise ProviderFailure("UPSTREAM_ERROR", "天气服务返回 500")
        days: list[NormalizedDay] = []
        cursor = start
        while cursor <= end:
            days.append(
                NormalizedDay(
                    date=cursor,
                    weather_text="多云",
                    temp_max=16.0,
                    temp_min=11.0,
                    precip_prob=30.0,
                    wind_scale=2,
                )
            )
            cursor += timedelta(days=1)
        return ProviderResult(
            source="open-meteo",
            attribution="天气数据由 Open-Meteo 提供（CC BY 4.0）",
            days=days,
        )


class EchoProvider(LLMProvider):
    """回声 provider：把收到的 messages 留下来，回放一份**合法**的计划 JSON。

    回放用 `mock.build_plan` 的产出，是为了让"计划合法"这一前提不成为变量：
    这里要验证的是注入，不是模型能力。它 `describe()` 标 `real=True`，
    这样 `run_generation` 走真模型分支（注入只在那条分支上）。
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


class LogCollector(logging.Handler):
    """收集 `ai-travel.weather` 的日志行，用来验证两个埋点真的打了。"""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


# ---------------------------------------------------------------- 工具


def plan_of(text: str) -> dict:
    return mock.build_plan(analyze_messages([{"role": "user", "content": text}]))


def weather_keys(node, path: str = "") -> list[str]:
    """递归找计划里是否出现天气字段（AC-3）。"""
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


def generate(client: TestClient, text: str, trace_id: str) -> tuple[dict, EchoProvider]:
    history = [{"role": "user", "content": text}]
    provider = EchoProvider(plan_of(text))
    app.state.provider = provider      # 只在本次请求生效：路由每次从 state 取
    resp = client.post(
        "/v1/plan/generate",
        json={"messages": history},
        headers={"X-Trace-Id": trace_id},
    )
    return resp.json(), provider


def last_user(prompt: list[dict[str, str]]) -> str:
    return next(m["content"] for m in reversed(prompt) if m["role"] == "user")


def has_block(prompt: list[dict[str, str]]) -> bool:
    return any("【已核实的天气预报】" in m["content"] for m in prompt)


def table_rows(block: str) -> list[str]:
    """取出注入块里的表格数据行（表头与分隔行排除）。"""
    rows: list[str] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] in ("日期", "---"):
            continue
        rows.append(line)
    return rows


def main() -> int:
    collector = LogCollector()
    weather_logger = logging.getLogger("ai-travel.weather")
    weather_logger.setLevel(logging.INFO)
    weather_logger.addHandler(collector)

    stub = WeatherStub()
    open_meteo.fetch = stub     # 唯一出网点换成替身；上面一层全是真代码
    history_single = [{"role": "user", "content": SINGLE}]

    with TestClient(app) as client:
        # ---------------------------------------------------------- A 默认关闭
        print("\n=== A 默认关闭（不配 WEATHER_INJECT_ON_GENERATION）===", flush=True)
        settings.weather.inject_on_generation = False
        weather_inject.reset_cache()
        stub.calls.clear()
        collector.lines.clear()

        body, provider = generate(client, SINGLE, "A-default-off")
        data = body.get("data") or {}
        draft = data.get("draft") or {}
        plan = draft.get("plan") or {}

        record("A.1 开关的默认值是 false（代码默认值，不依赖有没有人配）",
               WeatherSettings().inject_on_generation is False,
               f"dataclass 默认 = {WeatherSettings().inject_on_generation}")
        record("A.2 一次上游都没碰", len(stub.calls) == 0, f"上游调用 {len(stub.calls)} 次")
        record("A.3 prompt 里没有注入块", not has_block(provider.sent),
               f"messages={len(provider.sent)} 条")
        record("A.4 prompt 与基线逐字一致（关掉 = 等于没有这个功能）",
               provider.sent == build_plan_messages(history_single),
               "与 build_plan_messages(history) 全等")
        record("A.5 生成照常成功", body.get("code") == 0 and bool(plan),
               f"code={body.get('code')} 计划天数={len(plan.get('days') or [])}")
        record("A.6 埋点说了「为什么没注入」",
               any("weather_skipped" in line and "INJECT_DISABLED" in line for line in collector.lines),
               _skim(collector.lines))

        # ---------------------------------------------------------- B 注入生效
        print("\n=== B 开启注入 + 成都查得到 ===", flush=True)
        settings.weather.inject_on_generation = True
        weather_inject.reset_cache()
        stub.mode = "ok"
        stub.calls.clear()
        collector.lines.clear()

        body, provider = generate(client, SINGLE, "B-inject-ok")
        data = body.get("data") or {}
        plan = (data.get("draft") or {}).get("plan") or {}
        block = last_user(provider.sent)
        rows = table_rows(block)

        record("B.1 上游只查了 1 次（单城、不重试）", len(stub.calls) == 1,
               f"上游调用 {len(stub.calls)} 次，窗口 {_span(stub.calls)}")
        record("B.2 注入块进了**最终** prompt", "【已核实的天气预报】" in block,
               f"最后一条 user 消息 {len(block)} 字")
        record("B.3 覆盖城市写明了是成都", "覆盖城市：成都。" in block, _first_line(block, "覆盖城市"))
        record("B.4 表格里有已核实的三天", len(rows) == 3,
               f"数据行 {len(rows)} 条，首行={rows[0].strip() if rows else '—'}")
        record("B.5 用户原话逐字保留，且与注入块分段",
               block.rstrip().endswith(SINGLE) and "【用户需求】" in block,
               "以【用户需求】分段，原话在最后")
        record("B.6 system prompt 里没有运行时数据（规则段仍是纯规则）",
               "已核实的天气预报" not in provider.sent[0]["content"],
               f"system {len(provider.sent[0]['content'])} 字")
        record("B.7 aiNote 说明参考了哪份预报",
               "预报" in (plan.get("aiNote") or "") and "成都" in (plan.get("aiNote") or ""),
               (plan.get("aiNote") or "")[-80:])
        record("B.8 计划里**没有**天气字段（契约未改）",
               weather_keys(plan) == [], f"命中 {weather_keys(plan)}")
        record("B.9 埋点 weather_injected 带上了耗时与城市数",
               any(line.startswith("weather_injected") and "elapsedMs=" in line for line in collector.lines),
               _skim(collector.lines))

        # ---------------------------------------------------------- C 坐标未知
        print("\n=== C 多城：成都成功 + 坐标未知城市 ===", flush=True)
        weather_inject.reset_cache()
        stub.calls.clear()
        collector.lines.clear()

        body, provider = generate(client, MULTI, "C-partial")
        data = body.get("data") or {}
        plan = (data.get("draft") or {}).get("plan") or {}
        block = last_user(provider.sent)
        rows = table_rows(block)

        record(f"C.1 坐标未知的「{UNKNOWN_CITY}」没有发起上游请求",
               len(stub.calls) == 1, f"上游调用 {len(stub.calls)} 次（期望 1：只有成都）")
        record("C.2 prompt 明确写出该城没有预报、不得编造",
               f"请勿为它们编造天气：{UNKNOWN_CITY}。" in block,
               _first_line(block, "请勿"))
        record("C.3 表格里没有该城的任何一行（哪怕破折号也不行）",
               rows and not any(UNKNOWN_CITY in line for line in rows),
               f"数据行 {len(rows)} 条，全部属于 成都")
        record("C.4 aiNote 也如实说了未参考",
               f"{UNKNOWN_CITY}未取得预报" in (plan.get("aiNote") or ""),
               (plan.get("aiNote") or "")[-80:])
        record("C.5 部分成功仍然算成功（不是「要么全有要么全无」）",
               "覆盖城市：成都。" in block,
               "覆盖城市：成都")

        # ---------------------------------------------------------- D 上游 500
        print("\n=== D 上游 500（真失败，不是熔断）===", flush=True)
        weather_circuit.reset_all()
        weather_inject.reset_cache()
        stub.mode = "http_500"
        stub.calls.clear()
        collector.lines.clear()

        body, provider = generate(client, SINGLE, "D-upstream-500")
        data = body.get("data") or {}
        plan = (data.get("draft") or {}).get("plan") or {}

        record("D.1 只试了 1 次（失败不重试）", len(stub.calls) == 1,
               f"上游调用 {len(stub.calls)} 次")
        record("D.2 生成照常成功、计划不为空",
               body.get("code") == 0 and bool(plan.get("days")),
               f"code={body.get('code')} 计划天数={len(plan.get('days') or [])}")
        record("D.3 没有注入块（拿不到就不提，不留占位）", not has_block(provider.sent),
               f"messages={len(provider.sent)} 条")
        record("D.4 aiNote 里没有「参考了预报」这种假话",
               "预报" not in (plan.get("aiNote") or ""), (plan.get("aiNote") or "")[-40:])
        record("D.5 埋点 reason=UPSTREAM_ERROR",
               any("weather_skipped" in line and "UPSTREAM_ERROR" in line for line in collector.lines),
               _skim(collector.lines))

        # ---------------------------------------------------------- E 熔断
        print("\n=== E 熔断打开后：不再打上游，生成仍然成功 ===", flush=True)
        weather_circuit.reset_all()
        weather_inject.reset_cache()
        stub.mode = "http_500"
        stub.calls.clear()
        collector.lines.clear()

        for index in range(3):
            generate(client, SINGLE, f"E-fail-{index}")
        calls_after_failures = len(stub.calls)
        breaker = weather_circuit.breaker_for("open-meteo")
        record("E.1 连续 3 次软失败后熔断器打开",
               calls_after_failures == 3 and breaker.state == weather_circuit.OPEN,
               f"上游调用 {calls_after_failures} 次，熔断状态={breaker.state}")

        body, provider = generate(client, SINGLE, "E-circuit-open")
        data = body.get("data") or {}
        plan = (data.get("draft") or {}).get("plan") or {}
        record("E.2 熔断期间一次上游都不打（调用数冻结）",
               len(stub.calls) == calls_after_failures,
               f"调用数 {calls_after_failures} → {len(stub.calls)}")
        record("E.3 熔断期间生成仍然成功", body.get("code") == 0 and bool(plan.get("days")),
               f"code={body.get('code')} 计划天数={len(plan.get('days') or [])}")
        record("E.4 熔断期间也没有注入块", not has_block(provider.sent),
               "无【已核实的天气预报】")
        record("E.5 埋点能分辨「本地拦下的熔断」和「出网后的真失败」",
               any("weather_skipped" in line and "CIRCUIT_OPEN" in line for line in collector.lines),
               _skim(collector.lines))

    # ---------------------------------------------------------------- 汇总
    failed = [name for name, status, _ in RESULTS if status == "FAIL"]
    print("\n" + "=" * 78, flush=True)
    print(f"共 {len(RESULTS)} 项断言，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项", flush=True)
    if failed:
        print("失败项：" + "、".join(failed), flush=True)
    return 1 if failed else 0


def _first_line(block: str, keyword: str) -> str:
    for line in block.splitlines():
        if keyword in line:
            return line.strip()[:90]
    return "（没找到这一行）"


def _skim(lines: list[str]) -> str:
    hits = [line for line in lines if line.startswith("weather_")]
    # 取最后两条：熔断这类"后来才发生"的事实在末尾，只显示第一条会把它挡掉
    return "；".join(hits[-2:])[:200] if hits else "（没有天气埋点日志）"


def _span(calls: list[tuple[date, date]]) -> str:
    if not calls:
        return "—"
    start, end = calls[0]
    return f"{start}~{end}"


if __name__ == "__main__":
    raise SystemExit(main())
