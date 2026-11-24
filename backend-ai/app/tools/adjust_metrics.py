"""调整期天气的进程内观测（M3-3 · S5）。

## 为什么自己数，而不去解析日志

PM 的验收要求是"观测指标能够准确区分成功、失败、熔断和拦截"。日志里这四种确实都能看见，
但日志是**行**，不是**指标**：要从日志得出"灰度用户与非灰度用户的调用对比"，
得先做一遍解析与聚合，而那条解析链本身就是新的出错点（正则改一行，指标就错一类）。

所以这里用最笨也最稳的办法：**在决策的地方自增计数**。产品链路的判断一行不改，
只在"已经知道结果"的位置记一笔。

## 四类结果是互斥的，逐次二选一

`gray.allowed` / `gray.denied{reason}` 记录**闸门**；`outcome.*` 记录**注入尝试的结果**。
两者分开是因为它们回答不同问题：前者是"该不该给他开"，后者是"开了以后这次成没成"。
把它们揉成一个"错误率"，PM 明确说过不可以。

## 延迟用最近 N 次的分位数，不做全量直方图

进程内保留最近 500 次样本，按 nearest-rank 取 P50/P95。理由：
- 灰度期样本量小，全量直方图的分桶边界会先成为一个拍脑袋的决定；
- nearest-rank 在样本少时比插值更诚实（M3-2 的 AC-9 就是被插值坑过一次）。
代价是"上周的数据"看不到——那本来就该由日志/监控系统回答，不是进程内计数器。
"""

from __future__ import annotations

import threading
from collections import Counter, deque

# 保留的样本数：500 次约等于灰度期半天的量，够算 P95，内存可忽略
_SAMPLE_CAPACITY = 500

_lock = threading.Lock()
_gray = Counter()            # GRAY_ALLOWED / 各拒绝原因
_outcome = Counter()         # injected / no_weather / circuit_open / value_gate / failed
_upstream_calls = 0
_cities_queried = 0
_weather_ms: deque[int] = deque(maxlen=_SAMPLE_CAPACITY)
_total_ms: deque[int] = deque(maxlen=_SAMPLE_CAPACITY)


def reset() -> None:
    """清空全部计数。只给测试与"重新开始一轮灰度观察"用。"""
    global _upstream_calls, _cities_queried
    with _lock:
        _gray.clear()
        _outcome.clear()
        _upstream_calls = 0
        _cities_queried = 0
        _weather_ms.clear()
        _total_ms.clear()


def record_gray(allow: bool, reason: str) -> None:
    with _lock:
        _gray[reason if not allow else "GRAY_ALLOWED"] += 1


def record_outcome(kind: str, *, weather_ms: int = 0, total_ms: int = 0,
                   cities: int = 0, upstream: int = 0, timed: bool = False) -> None:
    """记一次注入尝试。

    `kind` 取值（互斥）：
    - `injected`      真的注入了
    - `no_weather`    没注入，但原因不属于下面两类（无日期/无城市/无数据…）
    - `circuit_open`  熔断期被本地拦下（**没有出网**）
    - `value_gate`    注入过，但模型抄了数值被闸门拒绝
    - `failed`        模型/解析/结构校验失败（与天气无关，单独计数才看得出回归）

    `timed=True` 才会把这次耗时计入分位数。**不要用"数值不等于 0"来代替这个标记**：
    本机替身下天气耗时真的是 0ms，那种 0 是有效样本；而 `gray_denied` 的 0 是"没查"，
    混进来会把 P50 拉低，得出一个看起来很快的假指标。
    """
    global _upstream_calls, _cities_queried
    with _lock:
        _outcome[kind] += 1
        _upstream_calls += max(int(upstream), 0)
        _cities_queried += max(int(cities), 0)
        if timed:
            _weather_ms.append(int(weather_ms))
            _total_ms.append(int(total_ms))


def _percentile(samples: list[int], p: float) -> int:
    """nearest-rank：样本少时比插值诚实。空样本返回 0。"""
    if not samples:
        return 0
    ordered = sorted(samples)
    rank = max(1, int(round(p / 100 * len(ordered) + 0.4999)))
    return ordered[min(rank, len(ordered)) - 1]


def snapshot() -> dict:
    """给 `/internal/metrics/weather-adjust` 的只读快照。"""
    with _lock:
        gray = dict(_gray)
        outcome = dict(_outcome)
        weather_samples = list(_weather_ms)
        total_samples = list(_total_ms)
        upstream = _upstream_calls
        cities = _cities_queried

    queried_total = gray.get("GRAY_ALLOWED", 0)
    # 缓存节省率 = 没出网的城市占比。只在"放行过的请求"上算，否则分母里混进大量
    # 根本没查的请求，会得出一个看起来很好看的假数字。
    cache_saved = 0.0
    if cities > 0:
        cache_saved = round(1 - upstream / cities, 4)

    return {
        "gray": {"allowed": queried_total, "denied": {k: v for k, v in gray.items()
                                                      if k != "GRAY_ALLOWED"}},
        "outcome": outcome,
        "upstream": {"calls": upstream, "cities": cities, "cacheSavedRate": cache_saved},
        "latencyMs": {
            "weather": {"p50": _percentile(weather_samples, 50),
                        "p95": _percentile(weather_samples, 95),
                        "samples": len(weather_samples)},
            "total": {"p50": _percentile(total_samples, 50),
                      "p95": _percentile(total_samples, 95),
                      "samples": len(total_samples)},
        },
    }
