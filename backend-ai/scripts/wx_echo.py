"""S3 脚本共用的小工具：天气上游替身、回声 provider、计划扫描。

**为什么单独一个模块**：故障矩阵和延迟测量要用的是**同一套**替身。两份拷贝迟早会漂移
（一处改了模式名，另一处还按旧名字断言，两边都绿但都没测真的东西）。
S2 的 `verify_weather_inject.py` 保持原样不动 —— 它是已验收的证据文件，不做重构。

## 替身的边界

只换 `open_meteo.fetch`（唯一出网点）。它上面一层 —— `fetch_forecast` 的
城市解析、窗口裁剪、熔断、归一化、`_assemble` —— 全是真代码。
所以这里能造出"上游超时/500/异常数据"，但造不出"我们自己判错了窗口"，
后者是单测的事。

## 回声 provider

把收到的 messages 留下来，回放一份用 `mock.build_plan` 生成的**合法**计划。
用同一个 provider 跑对照组与实验组，模型与网络这两个变量才是被固定住的。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Callable

from app.llm.provider import LLMProvider
from app.plan import mock
from app.plan.intent import analyze_messages
from app.tools.weather_providers.base import NormalizedDay, ProviderFailure, ProviderResult

# 上游模式
MODE_OK = "ok"
MODE_500 = "http_500"
MODE_SLOW = "slow"
MODE_ABNORMAL = "abnormal"

# 上游 500 用的 reason（M3-1 枚举，不新增）
REASON_UPSTREAM = "UPSTREAM_ERROR"


def ok_result(start: date, end: date) -> ProviderResult:
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


class WeatherStub:
    """`open_meteo.fetch` 的替身。

    `router` 按坐标决定这一城用哪种模式 —— 多城场景里"一慢一快"必须能分开造，
    而 `fetch` 只拿得到 lat/lon（城市名在更上层）。用坐标分流而不是给注入层加钩子：
    注入层不该为了测试多一个参数。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.mode = MODE_OK
        self.router: Callable[[float, float], str] | None = None
        self.slow_seconds = 5.0

    def reset(self, mode: str = MODE_OK, router: Callable[[float, float], str] | None = None) -> None:
        self.calls.clear()
        self.mode = mode
        self.router = router

    def reset_calls(self) -> None:
        self.calls.clear()

    async def __call__(self, *, lat, lon, start, end, **kwargs) -> ProviderResult:
        self.calls.append({"lat": lat, "lon": lon, "start": start, "end": end})
        mode = self.router(lat, lon) if self.router else self.mode

        if mode == MODE_500:
            raise ProviderFailure(REASON_UPSTREAM, "天气服务返回 500")
        if mode == MODE_SLOW:
            # 真睡，让注入层的预算真的生效：假超时（立刻返回）测不出"取消"这条路
            import asyncio

            await asyncio.sleep(self.slow_seconds)
            mode = MODE_OK
        if mode == MODE_ABNORMAL:
            # 上游 200 但字段全是空：归一化之后全部 verified=false，
            # 注入层必须认为"这城没数据"，而不是把「暂无数据」抄进表里
            return ProviderResult(
                source="open-meteo",
                attribution="天气数据由 Open-Meteo 提供（CC BY 4.0）",
                days=[NormalizedDay(date=start)],
            )
        return ok_result(start, end)


class EchoProvider(LLMProvider):
    """回声 provider：留 messages，回放合法计划。`real=True` 才会走注入分支。"""

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
    """收 `ai-travel.weather` 的日志行：埋点与耗时都从这里读。"""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def clear(self) -> None:
        self.lines.clear()

    def find(self, keyword: str) -> list[str]:
        return [line for line in self.lines if keyword in line]

    def elapsed_ms(self) -> int | None:
        """最近一条天气埋点里的 elapsedMs —— 这是**链路上**的天气查询耗时，不是函数微基准。"""
        for line in reversed(self.lines):
            if not line.startswith("weather_"):
                continue
            for piece in line.split():
                if piece.startswith("elapsedMs="):
                    try:
                        return int(piece.split("=", 1)[1])
                    except ValueError:
                        return None
        return None


def plan_of(text: str) -> dict:
    return mock.build_plan(analyze_messages([{"role": "user", "content": text}]))


def weather_keys(node, path: str = "") -> list[str]:
    """递归找计划里是否出现天气字段（D1：天气只进 prompt，不进计划）。"""
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


def last_user(prompt: list[dict[str, str]]) -> str:
    return next(m["content"] for m in reversed(prompt) if m["role"] == "user")


def has_block(prompt: list[dict[str, str]]) -> bool:
    return any("【已核实的天气预报】" in m["content"] for m in prompt)


def table_rows(block: str) -> list[str]:
    """注入块里的表格数据行（排除表头与分隔行）。"""
    rows: list[str] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] in ("日期", "---"):
            continue
        rows.append(line)
    return rows
