"""调整期天气注入（M3-3 · S2）：把 S1 的窗口接到 `run_adjust` 上。

链路：算窗口（`plan_weather_pairs`）→ 并发查前 N 个城市（复用 `weather_inject.query_forecasts`）
→ 压成摘要（`build_brief`）→ **按 `(日期, 城市)` 白名单过滤** → 渲染 + 调整场景附加约束
→ 塞进 user 消息最前面。

## 与生成期（M3-2）的三处不同，每一处都不是偏好而是必要

1. **窗口是交集**，不是"起点必须落在窗口内"。调整的行程常常已经开始（`IN_PROGRESS`），
   照搬生成期那条判定会把最典型的请求全部挡掉。详见 `adjust_window`。
2. **预算更小（1200ms）**。生成是 SSE，天气耗时只推迟"用户看到内容"；调整是同步 JSON，
   天气耗时 100% 叠加在白屏时间里。
3. **渲染必须过白名单**。查询用全局区间（为了复用同一套超时/取消语义），但区间可能
   比计划里的日期更宽，多查的那几天绝不能进 prompt —— 否则模型会把"计划外的日期"
   当成计划里的一天来处理。

## 失败一律等于"不注入"

`prepare_adjust_weather_block` 在任何情况下都不抛异常，失败返回 `block=None`。
调用方是产出调整建议的主链路，一个纯辅助上下文不该有能力把它弄挂。

## 埋点

`weather_adjust_injected` / `weather_adjust_skipped(reason=…, detail=逐城原因)`。
与 M3-2 的 `weather_injected` / `weather_skipped` 刻意分成四个名字：日志分析时必须能
区分链路，否则"今天天气查了多少次"会被重复计数。日志只记 traceId、城市数、日期数、
覆盖情况、耗时、上游次数 —— **不记**计划内容，也不记完整天气包（03 §4.2）。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Mapping

from app.config import settings
from app.tools.adjust_window import plan_weather_pairs
from app.tools.weather import TZ
from app.tools.weather_brief import (
    SKIP_DISABLED,
    SKIP_NO_DATA,
    InjectDecision,
    WeatherBrief,
    build_brief,
    render_brief_block,
)
from app.tools.weather_inject import SKIP_INJECT_DISABLED, InjectResult, query_forecasts

logger = logging.getLogger("ai-travel.weather")

# 调整场景的附加约束。不并进 M3-2 的 `_USAGE_RULES`：那段是生成场景写的，
# 而"用户没提到的项要保持原样"这条只在增量修改里才有意义。
_ADJUST_USAGE_RULES = """【本次是调整，不是重排】
- 只在用户这次的要求与天气有关时才参考上表；不要因为天气去改用户没有提到的行程项。
- 上表只用来说明「这样改是否更合理」，不要把它写进任何字段。"""

# 配置值不合法时只告警一次，避免每个请求都刷一行
_warned_max_cities = False

# 天气数值的识别式（M3-3 · S3）。
#
# 刻意**不复用** `contract.NUMERIC_UNIT_PATTERN`：那个正则服务于已冻结契约的 freeText 校验，
# 给它加 `%`/`级` 会改变生成链路已验收的行为。这里只认天气语境里会出现的单位，
# 不认"分钟/小时/元/公里"——那些是行程本身的单位，拦它们等于顺手改了调整规则。
#
# 也刻意**不认中文数字**（「十几度」「三四级」）：扩到中文数字会把「一起」「十分」这类
# 正常表达一起误伤，而调整链路上误伤的代价是"合法建议被拒，用户得重说一遍"，
# 比漏网更糟。这条限制写在设计稿 §4.3，验收时用抽样兜底，不假装挡住了。
WEATHER_VALUE_PATTERN = re.compile(
    r"[0-9０-９]+\s*(?:%|％|℃|°C|°|摄氏度|度|级|毫米|mm|MM|厘米|cm|米/秒|m/s|米每秒)"
)

# 只检查这四类自由文本（PM 批复：title / note / theme / summary）。
# 不含 city：城市名里出现数字是地名的正常形态；不含 startTime/endTime：HH:mm 本来就带数字。
_WATCHED_FIELDS = ("title", "note", "theme")



async def prepare_adjust_weather_block(
    plan_view: Mapping[str, Any],
    *,
    trace_id: str = "",
    today: date | None = None,
) -> InjectResult:
    """判断 → 查询 → 过滤 → 渲染。任何失败都返回"不注入"，绝不抛给调用方。"""
    started = time.perf_counter()
    cfg = settings.weather

    if not cfg.inject_on_adjust:
        return _skip(SKIP_INJECT_DISABLED, started, trace_id)
    if not cfg.enabled:
        return _skip(SKIP_DISABLED, started, trace_id)

    _warn_if_bad_max_cities(cfg.inject_max_cities)

    today = today or datetime.now(TZ).astimezone(TZ).date()
    window = plan_weather_pairs(plan_view, today=today, max_cities=cfg.inject_max_cities)
    if not window.allowed:
        return _skip(window.reason or SKIP_NO_DATA, started, trace_id)

    decision = InjectDecision(True, None, window.cities, window.days, window.start)
    # 用 replace 换掉预算而不是传参：这样 `_query` 内部那条"读 cfg.inject_timeout_ms"
    # 的逻辑一行都不用改，调整链路也就不用复制一套超时/取消语义
    budget_cfg = replace(cfg, inject_timeout_ms=int(cfg.inject_adjust_timeout_ms))
    results, upstream = await query_forecasts(decision, budget_cfg)

    brief = build_brief(results)
    if brief is None:
        return _skip(
            SKIP_NO_DATA, started, trace_id,
            cities=len(window.cities), upstream=upstream,
            reasons=_reasons_of(results),
        )

    filtered = filter_brief(brief, window.pairs)
    if filtered is None:
        # 查到了，但没有任何一行落在白名单里 —— 等同于"这次没有可用天气预报"
        return _skip(
            SKIP_NO_DATA, started, trace_id,
            cities=len(window.cities), upstream=upstream,
            reasons=_reasons_of(results),
        )

    block = render_brief_block(filtered) + "\n" + _ADJUST_USAGE_RULES
    elapsed = _ms(started)
    logger.info(
        "weather_adjust_injected trace=%s cities=%d pairs=%d covered=%d uncovered=%d rows=%d "
        "days=%d upstream=%d elapsedMs=%d",
        trace_id, len(window.cities), len(window.pairs), len(filtered.covered_cities),
        len(filtered.uncovered_cities), len(filtered.days), window.days, upstream, elapsed,
    )
    return InjectResult(
        block=block,
        brief=filtered,
        reason=None,
        elapsed_ms=elapsed,
        cities=len(window.cities),
        upstream=upstream,
    )


# ---------------------------------------------------------------- 白名单过滤


def filter_brief(
    brief: WeatherBrief,
    whitelist: tuple[tuple[str, str], ...],
) -> WeatherBrief | None:
    """只留下 `(日期, 城市)` 在计划里的行；一行都不剩时返回 `None`。

    排序沿用 `build_brief` 的结果（城市顺序 + 日期），这里只做减法，不重排。

    有数据但整座城市的行都被过滤掉时，把它补进 `uncovered_cities`：
    它确实有预报，只是没有**计划内日期**的预报。此时正确的措辞仍是"没有"，因为
    模型需要知道的只有"这些天我没有它的数据"；让一座城市从块里静默消失，
    模型就会默认它不需要天气——那比说错更危险（M3-2 已经踩过这个坑）。
    """
    allowed = {pair for pair in whitelist}
    rows = tuple(row for row in brief.days if (row.date, row.city) in allowed)
    if not rows:
        return None

    kept = {row.city for row in rows}
    covered = tuple(city for city in brief.covered_cities if city in kept)
    emptied = tuple(city for city in brief.covered_cities if city not in kept)
    return WeatherBrief(
        source=brief.source,
        attribution=brief.attribution,
        fetched_at=brief.fetched_at,
        days=rows,
        covered_cities=covered,
        uncovered_cities=tuple(brief.uncovered_cities) + emptied,
    )


# ---------------------------------------------------------------- 数值闸门（S3）


def validate_weather_values(summary: Any, ops: Any) -> list[str]:
    """注入过天气之后，检查自由文本里有没有把天气数值抄进去。返回人类可读问题列表。

    ## 为什么独立成一个函数，而不是给 `validate_ops` 加开关

    PM 的验收要求是"没注入时原校验器行为逐字不变"。**不动 `validate_ops` 是最强的证明**：
    它一个字符没改，所以不存在"开关没打开但分支走错了"这种可能。调用方只在
    `weather.injected` 为真时才调这里，闸门的存在性完全由调用点决定。

    ## 为什么必须拒绝，而不是自动修

    删掉违规文本等于悄悄改用户要的内容；静默放行等于让编造数字进界面；自动重试会让
    延迟翻倍且不确定（模型可能再犯）。而"输出不合规就在本地拒绝、让用户马上重说一遍"
    本来就是这个模块的既有处置方式（见 `adjust.py` 模块文档），这里沿用同一条路。

    ## 已知限制

    中文数字（「十几度」）不命中。扩正则会把「一起」「十分」这类正常中文一起误伤，
    在调整链路上得不偿失。见设计稿 §4.3 与验收 AC-A10。
    """
    problems: list[str] = []

    hit = _scan(summary)
    if hit:
        problems.append(f"summary 里出现了天气数值「{hit}」")

    for position, op in enumerate(ops if isinstance(ops, list) else [], start=1):
        if not isinstance(op, Mapping):
            continue
        for field in _WATCHED_FIELDS:
            hit = _scan(op.get(field))
            if hit:
                problems.append(f"第 {position} 项的 {field} 里出现了天气数值「{hit}」")
        for change in op.get("changes") or []:
            if not isinstance(change, Mapping):
                continue
            field = change.get("field")
            if field not in _WATCHED_FIELDS:
                continue
            hit = _scan(change.get("after"))
            if hit:
                problems.append(f"第 {position} 项把 {field} 改成了含天气数值「{hit}」的内容")
    return problems


def _scan(value: Any) -> str | None:
    """命中就返回命中的那一段（写进错误消息，便于用户和日志定位），否则 None。"""
    if not isinstance(value, str) or not value:
        return None
    match = WEATHER_VALUE_PATTERN.search(value)
    return match.group(0).strip() if match else None


# ---------------------------------------------------------------- 小工具


def _warn_if_bad_max_cities(value: int) -> None:
    """上限配成 0 或负数时给一条明确的配置告警。

    `plan_weather_pairs` 会保底 1 个城市（返回"一个都不查"等于让功能静默失效），
    但保底会掩盖配置错误，所以这里必须留个响亮的痕迹。
    """
    global _warned_max_cities
    if value > 0 or _warned_max_cities:
        return
    _warned_max_cities = True
    logger.warning(
        "WEATHER_INJECT_MAX_CITIES=%s 不合法（应为正整数），已按 1 处理；请检查部署配置",
        value,
    )


def _skip(
    reason: str,
    started: float,
    trace_id: str,
    *,
    cities: int = 0,
    upstream: int = 0,
    reasons: str = "",
) -> InjectResult:
    elapsed = _ms(started)
    logger.info(
        "weather_adjust_skipped trace=%s reason=%s cities=%d upstream=%d elapsedMs=%d%s",
        trace_id, reason, cities, upstream, elapsed,
        f" detail={reasons}" if reasons else "",
    )
    return InjectResult(
        block=None, brief=None, reason=reason,
        elapsed_ms=elapsed, cities=cities, upstream=upstream,
    )


def _reasons_of(results: list[tuple[str, Mapping[str, Any] | None]]) -> str:
    """`[(成都, payload)]` → `成都:CIRCUIT_OPEN,昆明:TIMEOUT`（只用于日志）。

    刻意与 `weather_inject._reasons_of` 各写一份而不是跨模块引用私有名：
    它只影响日志长相，不影响任何判断；而跨模块取私有名会让"有意复用"和
    "随手越界"在评审时看不出区别。
    """
    parts: list[str] = []
    for city, payload in results:
        if payload is None:
            parts.append(f"{city}:TIMEOUT")
        elif payload.get("ok"):
            parts.append(f"{city}:EMPTY")
        else:
            parts.append(f"{city}:{payload.get('reason') or 'UNKNOWN'}")
    return ",".join(parts)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
