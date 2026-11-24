"""M3-3 · S5：灰度闸门的判定与观测计数。

这个文件盯四件事：

1. **身份不可伪造**：签名不对 / 没签名 / 没密钥 → 一律不可信。
   只信 `X-User-Id` 这个字符串等于没有灰度。
2. **判定的顺序**：功能没开就说 `INJECT_DISABLED`，不能因为没带身份报成 `IDENTITY_UNTRUSTED`
   —— 两个结论指向完全不同的排障动作。
3. **生产 fail-closed**：`APP_ENV=prod` + 白名单空 → 不注入；dev/未声明 → 按实例级放行。
4. **计数互斥**：一次请求只落一个结果，四种（成功/无天气/熔断/被拦截）不串。
"""

from __future__ import annotations

import dataclasses

import pytest

from app.config import Settings
from app.tools import adjust_metrics, inject_gate
from app.tools.inject_gate import (
    DENY_INJECT_DISABLED,
    DENY_KILLED,
    DENY_NOT_WHITELISTED,
    DENY_NO_WHITELIST,
    DENY_PROVIDER_DISABLED,
    DENY_UNTRUSTED,
    GRAY_ALLOWED,
    Identity,
)

SECRET = "s3cr3t-for-tests"


def make_settings(**weather) -> Settings:
    """造一份独立配置，**不改全局 `settings`**（否则会串测试）。"""
    base = Settings(env="dev")
    return dataclasses.replace(base, weather=dataclasses.replace(base.weather, **weather))


def trusted(user_id: str = "7") -> Identity:
    return Identity(user_id, True, "")


# ---------------------------------------------------------------- 身份校验


def test_signature_matches_java_constant() -> None:
    """跨语言协议钉死：Java 侧 `AiServiceIdentityHeaderTest` 用的是同一个字面量。

    任何一方改了算法（编码、大小写、分隔符），这里或那里会立刻红；
    否则灰度只会"悄悄不生效"，排查成本极高。
    """
    assert inject_gate.sign("test-secret-请勿用于生产", "42", "t-9") == (
        "38373c73cd9f91a55fabfed9cbdc336652e6b808296703ed9251368aef5088bc"
    )


def test_signature_roundtrip() -> None:
    sig = inject_gate.sign(SECRET, "7", "t-1")
    headers = {inject_gate.HEADER_USER: "7", inject_gate.HEADER_SIG: sig}
    identity = inject_gate.verify_identity(headers, secret=SECRET, trace_id="t-1")
    assert identity.trusted and identity.user_id == "7"


def test_signature_is_bound_to_trace() -> None:
    """签名绑定 traceId：把 A 请求的签名搬到 B 请求上不成立。"""
    sig = inject_gate.sign(SECRET, "7", "t-1")
    headers = {inject_gate.HEADER_USER: "7", inject_gate.HEADER_SIG: sig}
    assert inject_gate.verify_identity(headers, secret=SECRET, trace_id="t-2").trusted is False


def test_signature_rejects_other_secret() -> None:
    sig = inject_gate.sign("another-secret", "7", "t")
    headers = {inject_gate.HEADER_USER: "7", inject_gate.HEADER_SIG: sig}
    assert inject_gate.verify_identity(headers, secret=SECRET, trace_id="t").trusted is False


def test_missing_secret_means_untrusted() -> None:
    """服务没配密钥 → 谁都不能信。这是 fail-closed 的第一道。"""
    sig = inject_gate.sign("", "7", "")
    headers = {inject_gate.HEADER_USER: "7", inject_gate.HEADER_SIG: sig}
    identity = inject_gate.verify_identity(headers, secret="", trace_id="")
    assert identity.trusted is False
    assert identity.user_id == "7"          # 保留原值只为排障，不代表可信


@pytest.mark.parametrize("headers", [
    {},                                                      # 什么都没有
    {inject_gate.HEADER_USER: "7"},                          # 只有身份，没有签名
    {inject_gate.HEADER_SIG: "deadbeef"},                    # 只有签名
    {inject_gate.HEADER_USER: "abc", inject_gate.HEADER_SIG: "x"},   # 非数字身份
    {inject_gate.HEADER_USER: "", inject_gate.HEADER_SIG: "x"},
])
def test_forged_or_incomplete_identity_is_untrusted(headers: dict) -> None:
    assert inject_gate.verify_identity(headers, secret=SECRET, trace_id="t").trusted is False


def test_verify_never_raises_on_weird_input() -> None:
    """请求头是外部输入，校验函数不该有能力抛异常把调整请求打挂。"""
    assert inject_gate.verify_identity(
        {inject_gate.HEADER_USER: " 7 ", inject_gate.HEADER_SIG: "zz", }, secret=SECRET,
    ).trusted is False


# ---------------------------------------------------------------- 白名单解析


def test_whitelist_tolerates_spaces_and_empty_items() -> None:
    assert inject_gate.parse_whitelist(" 7, 9 ,, ") == frozenset({"7", "9"})


def test_whitelist_empty() -> None:
    assert inject_gate.parse_whitelist("") == frozenset()
    assert inject_gate.parse_whitelist(None) == frozenset()   # type: ignore[arg-type]


# ---------------------------------------------------------------- 判定矩阵


def test_switch_off_wins_over_missing_identity() -> None:
    """顺序：总开关优先。排障时这句话是"去改配置"，不是"去看网关"。"""
    decision = inject_gate.decide(Identity(), settings=make_settings(inject_on_adjust=False))
    assert decision.reason == DENY_INJECT_DISABLED


def test_provider_disabled_after_switch() -> None:
    decision = inject_gate.decide(
        Identity(), settings=make_settings(inject_on_adjust=True, provider="none")
    )
    assert decision.reason == DENY_PROVIDER_DISABLED


def test_kill_switch_denies_even_for_whitelisted_user() -> None:
    decision = inject_gate.decide(
        trusted(), settings=make_settings(inject_on_adjust=True, provider="open-meteo"),
        kill_active=True,
    )
    assert decision.reason == DENY_KILLED


def test_production_without_whitelist_is_closed() -> None:
    cfg = make_settings(inject_on_adjust=True, provider="open-meteo", inject_adjust_users="")
    settings = dataclasses.replace(cfg, env="prod")
    assert inject_gate.decide(trusted(), settings=settings, kill_active=False).reason == DENY_NO_WHITELIST


def test_dev_without_whitelist_is_instance_wide() -> None:
    """S4 的 63 项矩阵就靠这条继续可跑（测试环境未声明 env）。"""
    cfg = make_settings(inject_on_adjust=True, provider="open-meteo", inject_adjust_users="")
    for env in ("dev", "test", ""):
        settings = dataclasses.replace(cfg, env=env)
        decision = inject_gate.decide(Identity(), settings=settings, kill_active=False)
        assert decision.allow and decision.reason == GRAY_ALLOWED


def test_whitelisted_user_is_allowed() -> None:
    cfg = make_settings(inject_on_adjust=True, provider="open-meteo", inject_adjust_users="7,9")
    assert inject_gate.decide(trusted("9"), settings=cfg, kill_active=False).allow is True


def test_untrusted_identity_is_denied_when_whitelist_configured() -> None:
    cfg = make_settings(inject_on_adjust=True, provider="open-meteo", inject_adjust_users="7")
    decision = inject_gate.decide(Identity("7", False, "签名不对"), settings=cfg, kill_active=False)
    assert decision.reason == DENY_UNTRUSTED


def test_trusted_but_not_listed_is_denied() -> None:
    cfg = make_settings(inject_on_adjust=True, provider="open-meteo", inject_adjust_users="9")
    decision = inject_gate.decide(trusted("7"), settings=cfg, kill_active=False)
    assert decision.reason == DENY_NOT_WHITELISTED


# ---------------------------------------------------------------- 急停


def test_kill_file_absent(tmp_path) -> None:
    assert inject_gate.kill_switch_active(str(tmp_path / "nope")) is False
    assert inject_gate.kill_switch_active("") is False


def test_kill_file_present(tmp_path) -> None:
    flag = tmp_path / "stop-weather"
    flag.write_text("1", encoding="utf-8")
    assert inject_gate.kill_switch_active(str(flag)) is True


# ---------------------------------------------------------------- 观测


def test_metrics_gray_and_outcome_are_separate() -> None:
    adjust_metrics.reset()
    adjust_metrics.record_gray(True, GRAY_ALLOWED)
    adjust_metrics.record_gray(False, DENY_NOT_WHITELISTED)
    adjust_metrics.record_outcome("injected", weather_ms=12, total_ms=140, cities=2,
                                 upstream=1, timed=True)
    snap = adjust_metrics.snapshot()
    assert snap["gray"]["allowed"] == 1
    assert snap["gray"]["denied"] == {DENY_NOT_WHITELISTED: 1}
    assert snap["outcome"] == {"injected": 1}
    assert snap["latencyMs"]["weather"]["p50"] == 12
    assert snap["upstream"] == {"calls": 1, "cities": 2, "cacheSavedRate": 0.5}


def test_metrics_outcomes_do_not_collide() -> None:
    """成功/失败/熔断/拦截必须是四个桶，不能揉成一个"错误率"。"""
    adjust_metrics.reset()
    for kind in ("injected", "no_weather", "circuit_open", "gray_denied"):
        adjust_metrics.record_outcome(kind)
    assert adjust_metrics.snapshot()["outcome"] == {
        "injected": 1, "no_weather": 1, "circuit_open": 1, "gray_denied": 1,
    }


def test_percentile_nearest_rank() -> None:
    """nearest-rank：20 个样本的 P95 是第 19 个，不是插值出来的小数。"""
    adjust_metrics.reset()
    for value in range(1, 21):
        adjust_metrics.record_outcome("injected", weather_ms=value, total_ms=value, timed=True)
    snap = adjust_metrics.snapshot()
    assert snap["latencyMs"]["weather"]["p50"] == 10
    assert snap["latencyMs"]["weather"]["p95"] == 19
    assert snap["latencyMs"]["weather"]["samples"] == 20


def test_snapshot_empty_is_zeros_not_crash() -> None:
    adjust_metrics.reset()
    snap = adjust_metrics.snapshot()
    assert snap["gray"] == {"allowed": 0, "denied": {}}
    assert snap["latencyMs"]["weather"]["p50"] == 0
    assert snap["upstream"]["cacheSavedRate"] == 0.0
