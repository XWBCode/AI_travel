"""生成期天气注入（M3-2 · S2）：把 S1 的纯函数接到 `run_generation` 上。

链路：判断能否注入（`can_inject`）→ 并行查前 N 个城市（`fetch_forecast`）→
压成摘要（`build_brief`）→ 渲染成一段文本（`render_brief_block`）→ 塞进 user 消息。

本模块**只读** M3-1 的 `fetch_forecast`，不改它的缓存、熔断、降级枚举：
M3-1 已验收，PM 明确要求不为了 M3-2 顺手重构那条线。

## 默认关闭

`WEATHER_INJECT_ON_GENERATION` 默认 false。关闭时本模块在**第一次读配置**就返回，
连 `can_inject` 都不调，生成链路的行为与 M3-2 之前逐字相同（可用"同一输入两次生成的
messages 相同"断言）。想打开必须在部署配置里显式写 —— 外部依赖不该被默认打开。

## 超时是"用已完成的部分"，不是"整批作废"

用 `asyncio.wait(..., timeout=budget)` 而不是 `wait_for(gather(...))`：
后者超时会连同已经拿到的结果一起丢掉。慢的往往只有一座城，把快的那两座一起扔掉，
等于让一个慢城市拖垮整次注入。用户侧的代价只有首字延迟，硬预算 2 秒。

## 埋点只有两个

`weather_injected` / `weather_skipped(reason=…)`。日志只记 traceId、城市数、天数、
耗时、上游调用次数 —— **不记**对话原文，也不记完整天气包（03 §7.2）。

`reason` 取值：`INJECT_DISABLED`（开关没开）、`PROVIDER_DISABLED`（没配数据源）、
`NO_CITY` / `NO_START_DATE` / `BAD_START_DATE` / `DATE_OUT_OF_RANGE`（`can_inject`
的排除项，语义与 M3-1 一致）、`NO_DATA`（查了，但一个可用字段都没拿到；日志会附上**每个城市**的具体原因，
例如 `detail=成都:CIRCUIT_OPEN`，这样熔断与真 500 在埋点里也分得开）。
这些是**日志字段**，不进任何对外契约，所以不需要与 `weather_reasons` 一一对应；
但前四个刻意复用 M3-1 的枚举名，避免同一个概念出现两套说法。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from app.config import settings
from app.plan.contract import NUMERIC_UNIT_PATTERN, load_schema
from app.tools.weather import TZ, fetch_forecast
from app.tools.weather_reasons import (
    R_CIRCUIT_OPEN,
    R_CITY_UNKNOWN,
    R_DATE_OUT_OF_RANGE,
    R_NO_CITY,
    R_NO_START_DATE,
    R_PROVIDER_DISABLED,
)
from app.tools.weather_brief import (
    SKIP_DISABLED,
    SKIP_NO_DATA,
    InjectDecision,
    WeatherBrief,
    brief_ai_note,
    build_brief,
    can_inject,
    render_brief_block,
)

logger = logging.getLogger("ai-travel.weather")

# 开关没开。单独一个值而不是复用 PROVIDER_DISABLED：
# 「我们没开这个功能」和「配了但数据源没启用」是两件事，运维排查时要分得清
SKIP_INJECT_DISABLED = "INJECT_DISABLED"

# 进程内短缓存的条目上限。生成链路同时在跑的场次很少，条目再多也只是占内存；
# 满了就整体清空 —— 清零成本是 O(1)，比维护 LRU 链表更划算
_CACHE_MAX = 32
_cache: dict[tuple[str, str, str, int], tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class InjectResult:
    """一次注入尝试的完整结果。

    `block` 为 None 代表"这次不注入"，`reason` 说明为什么；两者恒有一个为空。
    `upstream` 按结果推算的**真实出网**城市数：命中缓存、坐标未知、熔断这类在本地
    就被拦下的都算 0。被取消的超时任务无法判定，也不计入（只用于日志与验收断言）。
    """

    block: str | None
    brief: WeatherBrief | None
    reason: str | None
    elapsed_ms: int = 0
    cities: int = 0
    upstream: int = 0

    @property
    def injected(self) -> bool:
        return self.block is not None


# ---------------------------------------------------------------- 主流程


async def prepare_weather_block(
    slots: Mapping[str, Any],
    *,
    trace_id: str = "",
    today: date | None = None,
) -> InjectResult:
    """判断 → 查询 → 压摘要 → 渲染。任何失败都返回"不注入"，绝不抛给调用方。

    这是一个 async 函数而不是在生成器里内联：`run_generation` 是 async generator，
    在它里面 `await` 一个可能抛异常的第三方调用，一旦异常穿透就会让整条 SSE 中断。
    收口在这里，生成器只需要判断 `block` 是否为空。
    """
    started = time.perf_counter()
    cfg = settings.weather

    if not cfg.inject_on_generation:
        return _skip(SKIP_INJECT_DISABLED, started, trace_id)
    if not cfg.enabled:
        # 开关开了但数据源是 none：不查、不报错，行为同"没天气"
        return _skip(SKIP_DISABLED, started, trace_id)

    today = today or datetime.now(TZ).astimezone(TZ).date()
    decision = can_inject(slots, today=today, max_cities=cfg.inject_max_cities)
    if not decision.allowed:
        return _skip(decision.reason or SKIP_NO_DATA, started, trace_id)

    results, upstream = await _query(decision, cfg)
    brief = build_brief(results)
    if brief is None:
        # 查了但一个可用字段都没有：同样视为"没天气"，不是什么"降级状态"。
        # 把每个城市的具体原因带上 —— 只说一句 UPSTREAM_ERROR 的话，
        # 熔断（本地拦下、没出网）和真 500（出网了、被拒）在日志里长得一模一样，
        # 那正是我们花了一整轮才在展示层修掉的那种混淆。
        return _skip(
            SKIP_NO_DATA, started, trace_id,
            cities=len(decision.cities), upstream=upstream,
            reasons=_reasons_of(results),
        )

    block = render_brief_block(brief)
    elapsed = _ms(started)
    logger.info(
        "weather_injected trace=%s cities=%d covered=%d uncovered=%d days=%d upstream=%d elapsedMs=%d",
        trace_id, len(decision.cities), len(brief.covered_cities),
        len(brief.uncovered_cities), len(brief.days), upstream, elapsed,
    )
    return InjectResult(
        block=block,
        brief=brief,
        reason=None,
        elapsed_ms=elapsed,
        cities=len(decision.cities),
        upstream=upstream,
    )


async def _query(
    decision: InjectDecision,
    cfg: Any,
) -> tuple[list[tuple[str, Mapping[str, Any] | None]], int]:
    """并行查所有候选城市。返回 `[(城市, payload)]` 与真实出网次数。

    单个城市的异常在这里就地消化成 `None`（= 该城没数据），不连累其他城市：
    `fetch_forecast` 自身已经把可预期失败转成 `{"ok": False, ...}`，
    这里兜的是"连它都炸了"的意外情况。
    """
    tasks = {asyncio.ensure_future(_fetch_city(city, decision, cfg)): city
             for city in decision.cities}
    budget = max(int(cfg.inject_timeout_ms), 0) / 1000
    done, pending = await asyncio.wait(tasks, timeout=budget or None)

    timed_out: list[tuple[str, Mapping[str, Any] | None]] = []
    if pending:
        # 超预算的城市直接取消：留着它们继续跑既不会进 prompt，还占着连接。
        # 但它们**要留在结果里**（payload=None）：让渲染层把这几座城写进
        # "本次没有拿到预报，请勿编造"那一句。否则模型看到的是一张"什么也没提"的表，
        # 它会默认这几座城本来就不需要天气——沉默比说错话更危险。
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        timed_out = [(tasks[task], None) for task in pending]

    results: list[tuple[str, Mapping[str, Any] | None]] = list(timed_out)
    upstream = 0
    for task in done:
        city = tasks[task]
        try:
            payload, cached = task.result()
        except Exception:  # noqa: BLE001 —— 单城失败不能穿透成整次生成的失败
            logger.warning("weather inject 单城失败 city=%s", city, exc_info=True)
            payload, cached = None, False
        if _went_upstream(payload, cached):
            upstream += 1
        results.append((city, payload))

    # asyncio.wait 返回的是 set，顺序丢失；按候选城市原顺序复原，
    # 保证同样输入渲染出同样文本（否则断言和复现都会变成看运气）
    order = {city: index for index, city in enumerate(decision.cities)}
    results.sort(key=lambda item: order.get(item[0], 0))
    return results, upstream


async def _fetch_city(
    city: str,
    decision: InjectDecision,
    cfg: Any,
) -> tuple[dict[str, Any], bool]:
    """查一个城市，返回 `(payload, 是否命中缓存)`。

    走到 `fetch_forecast` 之前不做本地城市解析：坐标表在它内部（`resolve_city`），
    解析不出来会返回 `CITY_UNKNOWN` 且**不发请求**。在这里再解析一遍，
    等于把「坐标未知就不出网」这条保证放进两个地方，改一处就会漏一处。
    """
    key = (str(cfg.provider), city, decision.start.isoformat(), decision.days)  # type: ignore[union-attr]
    cached = _cache_get(key)
    if cached is not None:
        return cached, True

    payload = await fetch_forecast(
        city,
        decision.start,
        decision.days,
        provider=cfg.provider,
        timeout_seconds=cfg.timeout_seconds,
        connect_timeout_seconds=cfg.connect_timeout_seconds,
        settings=cfg,
    )
    # 只缓存成功结果。失败不缓存：下一次生成时上游可能已经好了，
    # 把一次 500 记 60 秒，等于把一次偶发故障拖成一段时间内的确定性失败
    if payload.get("ok"):
        _cache_put(key, payload, int(cfg.inject_cache_seconds))
    return payload, False


# ---------------------------------------------------------------- aiNote 附注


def attach_ai_note(plan: dict[str, Any], brief: WeatherBrief) -> bool:
    """把"参考了哪份预报"这句话追加进 `aiNote`，返回是否真的追加了。

    三个刻意的取舍：

    1. **由程序拼，不让模型写**。模型的数字随时可能是编的，而这句话的全部意义
       就是"我们确实用了哪一份数据"。
    2. **在 `verify()` 之后追加**。这句话是我们加的事实性附注；万一它不合
       `freeText` 规则（数字+单位、超长），正确处置是**不加**，而不是把一份
       本来合格的计划判成不合格。所以这里自带一次自检。
    3. **不自检就追加是不行的**。`freeText` 的规则在契约里（`maxLength` 500 +
       禁"数字+单位"），我们写的日期串里有一堆数字，今天不触发不代表改版后不触发。
    """
    sentence = brief_ai_note(brief)
    if NUMERIC_UNIT_PATTERN.search(sentence):
        logger.warning("aiNote 附注含数字+单位，已跳过：%s", sentence[:60])
        return False

    current = plan.get("aiNote")
    current = current if isinstance(current, str) else ""
    merged = f"{current} {sentence}".strip() if current else sentence
    limit = _ai_note_max_length()
    if limit and len(merged) > limit:
        logger.warning("aiNote 追加后超过契约上限 %d 字，已跳过（原长 %d）", limit, len(current))
        return False

    plan["aiNote"] = merged
    return True


def _ai_note_max_length() -> int:
    """从契约里读 `aiNote` 的长度上限，读不到返回 0（= 不检查）。"""
    try:
        value = load_schema("travel_plan_v1.json")["$defs"]["freeText"].get("maxLength")
        return int(value) if value else 0
    except (KeyError, TypeError, ValueError, FileNotFoundError):
        return 0


# ---------------------------------------------------------------- 缓存


def _cache_get(key: tuple[str, str, str, int]) -> dict[str, Any] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, payload = entry
    if expires_at <= time.monotonic():
        _cache.pop(key, None)
        return None
    return payload


def _cache_put(key: tuple[str, str, str, int], payload: dict[str, Any], ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.monotonic() + ttl_seconds, payload)


def reset_cache() -> None:
    """清空短缓存。测试与"换了数据源"这类场景需要，产品代码不调。"""
    _cache.clear()


# ---------------------------------------------------------------- 对 M3-3 的复用出口

async def query_forecasts(
    decision: InjectDecision,
    cfg: Any,
) -> tuple[list[tuple[str, Mapping[str, Any] | None]], int]:
    """并发查 `decision.cities`，返回 `[(城市, payload)]` 与真实出网次数。

    M3-3 的调整链路复用这一个入口，而不是自己再写一套并发查询。理由是两个语义
    已经在 M3-2 用一整轮修对了，重写一遍等于让它们再长一个版本、再犯一次同样的错：

    1. **超时用已完成的部分**（`asyncio.wait` 而不是 `wait_for(gather)`）；
    2. **被取消的城市必须留在结果里**（payload=None），这样渲染层能把它写进
       "本次没有拿到预报，请勿编造"，而不是让它静默消失。

    这里只把 `_query` 抬成公开名，**不改它的任何行为**；缓存也在这条路径里
    （`_fetch_city` 自己读写 `_cache`），所以调整链路天然与生成链路共享同一份短缓存，
    不需要另开一套。预算由传入的 `cfg.inject_timeout_ms` 决定——调整链路用
    `dataclasses.replace(cfg, inject_timeout_ms=...)` 换成本链路的小预算，
    也就不必在这里多加一个预算参数。
    """
    return await _query(decision, cfg)


# ---------------------------------------------------------------- 小工具


# 这几个 reason 代表"本地就拦下了，根本没发请求"。用来区分"查了但没成"和"压根没查"：
# 前者要算进上游调用数，后者不能算——否则"坐标未知的城市不发起上游请求"这条
# 在日志与断言里都会显示成"调用了 1 次"，看起来像是发了请求
_PRE_NETWORK_REASONS = frozenset({
    R_CITY_UNKNOWN, R_PROVIDER_DISABLED, R_NO_CITY, R_NO_START_DATE,
    R_DATE_OUT_OF_RANGE, R_CIRCUIT_OPEN,
})


def _went_upstream(payload: Mapping[str, Any] | None, cached: bool) -> bool:
    """这次查询有没有真的出网。

    保守口径：被取消的超时任务、异常穿透的调用都算"无法判定"，计 0。
    这个数字只用于日志与验收断言，宁可比实际小，也不要虚报成"出了网"。
    """
    if cached or payload is None:
        return False
    if payload.get("ok"):
        return True
    return str(payload.get("reason") or "") not in _PRE_NETWORK_REASONS


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
        "weather_skipped trace=%s reason=%s cities=%d upstream=%d elapsedMs=%d%s",
        trace_id, reason, cities, upstream, elapsed,
        f" detail={reasons}" if reasons else "",
    )
    return InjectResult(
        block=None, brief=None, reason=reason,
        elapsed_ms=elapsed, cities=cities, upstream=upstream,
    )


def _reasons_of(results: list[tuple[str, Mapping[str, Any] | None]]) -> str:
    """`[(成都, payload)]` → `成都:CIRCUIT_OPEN,昆明:UPSTREAM_ERROR`（只用于日志）。"""
    parts: list[str] = []
    for city, payload in results:
        if payload is None:
            parts.append(f"{city}:TIMEOUT")
            continue
        if payload.get("ok"):
            parts.append(f"{city}:EMPTY")
            continue
        parts.append(f"{city}:{payload.get('reason') or 'UNKNOWN'}")
    return ",".join(parts)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
