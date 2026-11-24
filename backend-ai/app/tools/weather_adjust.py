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
