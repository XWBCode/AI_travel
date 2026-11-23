"""天气上游熔断器。

## 为什么必须有

和风天气错误码文档原文：

> 当错误发生时，请暂停请求并进行排查。你不应该放任错误的继续发生，
> 否则这些错误的请求看起来是一种DDoS攻击，极端情况下，我们的安全策略可能冻结你的帐号。

也就是说"失败了继续重试"不只是浪费配额，而是**可能把账号搞没**。所以这里有两条硬规则：

1. **`hard` 错误立刻熔断**（401 / 403 / 429 / 凭据缺失）。这类错误重试只会更糟：
   403 里的 NO CREDIT / INVALID HOST / ACCOUNT SUSPENSION 全都是"配置/账单问题"，
   再请求一万次也不会变好，只会加速冻结。
2. **软错误连续 N 次才熔断**（5xx / 超时 / 结构异常）。单个超时很常见，不该立刻停服。

熔断打开期间**不发任何请求**，直接降级成 `available=false`。冷却结束后放**一个**试探请求
（half-open），成功就恢复，失败就把冷却时间翻倍——这就是指数退避，只是退避的是"下次允许
尝试的时间点"而不是重试间隔。

## 与 Java 侧超时的关系

Java 侧 `AiServiceClient.weatherTimeout` 是 5s。熔断只在 Python 进程内，打开时**立即返回**，
所以它反而是"让 Java 不要等到超时"的手段。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("ai-travel.weather.circuit")

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """按 provider 名的进程内熔断器。"""

    name: str
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0
    max_cooldown_seconds: float = 1800.0

    _state: str = field(default=CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _open_until: float = field(default=0.0, init=False)
    _current_cooldown: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def allow(self, now: float) -> bool:
        """现在能不能发请求。冷却结束后的第一次放行会把状态置为 half-open。"""
        with self._lock:
            if self._state == CLOSED:
                return True
            if now >= self._open_until:
                self._state = HALF_OPEN
                logger.info("熔断器 %s 进入半开，放行一个试探请求", self.name)
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state != CLOSED:
                logger.info("熔断器 %s 试探成功，恢复", self.name)
            self._state = CLOSED
            self._failures = 0
            self._current_cooldown = 0.0
            self._open_until = 0.0

    def record_failure(self, now: float, *, hard: bool = False) -> None:
        with self._lock:
            if hard:
                # 配置/账单类错误：直接跳到最长冷却，不要用"再试一次"赌它自己会好
                self._current_cooldown = self.max_cooldown_seconds
                self._state = OPEN
                self._open_until = now + self._current_cooldown
                logger.warning("熔断器 %s 因硬错误打开，冷却 %.0fs", self.name, self._current_cooldown)
                return

            self._failures += 1
            if self._state == HALF_OPEN:
                # 试探失败：冷却翻倍
                self._current_cooldown = min(
                    self.max_cooldown_seconds,
                    (self._current_cooldown or self.cooldown_seconds) * 2,
                )
                self._state = OPEN
                self._open_until = now + self._current_cooldown
                logger.warning("熔断器 %s 试探失败，冷却加倍到 %.0fs", self.name, self._current_cooldown)
                return

            if self._failures >= self.failure_threshold:
                self._current_cooldown = self._current_cooldown or self.cooldown_seconds
                self._state = OPEN
                self._open_until = now + self._current_cooldown
                logger.warning(
                    "熔断器 %s 连续失败 %d 次，打开 %.0fs", self.name, self._failures, self._current_cooldown
                )

    @property
    def state(self) -> str:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures


_BREAKERS: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def breaker_for(
    provider: str,
    *,
    failure_threshold: int = 3,
    cooldown_seconds: float = 300.0,
) -> CircuitBreaker:
    """取某个 provider 的熔断器（懒创建）。参数只在首次创建时生效。"""
    with _REGISTRY_LOCK:
        breaker = _BREAKERS.get(provider)
        if breaker is None:
            breaker = CircuitBreaker(
                name=provider,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            _BREAKERS[provider] = breaker
        return breaker


def reset_all() -> None:
    """仅用于测试：清空注册表，避免用例之间互相影响（这正是 BUG-017 那类问题的来源）。"""
    with _REGISTRY_LOCK:
        _BREAKERS.clear()
