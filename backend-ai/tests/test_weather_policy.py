"""数据源合规闸门：环境 × provider 的完整组合矩阵。

这类测试的价值不在"跑通"，而在于**锁住不允许的组合**。
如果哪天有人为了让本地方便，把 `open_meteo_allowed` 改成 `return True`，
这里必须红。所以每条"应当拒绝"的用例都断言了异常类型与消息关键词，
而不是只断言"抛了异常"——只断言抛异常，连拼写错误导致的 KeyError 也会算通过。
"""

from __future__ import annotations

import pytest

from app.tools import weather_policy
from app.tools.weather_policy import WeatherPolicyError


# ============ 允许的组合（必须能启动）============

@pytest.mark.parametrize("env", ["dev", "test"])
@pytest.mark.parametrize("provider", ["open-meteo", "OPEN_METEO", "Open-Meteo", "openmeteo", " open-meteo "])
def test_open_meteo_allowed_in_dev_and_test(env, provider):
    """dev/test 允许 Open-Meteo，且大小写/下划线/空格的写法都认。"""
    weather_policy.assert_provider_allowed(env, provider)  # 不抛即通过


@pytest.mark.parametrize("provider", ["none", "qweather"])
@pytest.mark.parametrize("env", ["dev", "test", "prod", "", None, "staging"])
def test_other_providers_never_blocked(env, provider):
    """闸门只管 Open-Meteo。none / qweather 在任何环境下都必须能启动，
    否则这道闸门会变成"任何环境配置错了都起不来"的噪音。"""
    weather_policy.assert_provider_allowed(env, provider)


# ============ 拒绝的组合（必须起不来）============

@pytest.mark.parametrize("env", ["prod", "production", "PRD", "prd", "staging", "uat", "", None])
@pytest.mark.parametrize("provider", ["open-meteo", "OPEN_METEO", "openmeteo"])
def test_open_meteo_rejected_outside_dev_and_test(env, provider):
    """未声明环境、拼错的环境、以及一切非 dev/test 环境都必须拒绝。

    `prd` 这种拼写错误是最危险的：如果按"看不懂就当 dev"处理，
    生产上就会出现一个看起来完全正常的合规缺口。
    """
    with pytest.raises(WeatherPolicyError) as exc:
        weather_policy.assert_provider_allowed(env, provider)
    message = str(exc.value)
    assert "Open-Meteo" in message
    assert "dev / test" in message


def test_rejection_message_carries_the_actual_env_value():
    """报错必须带出**实际读到的值**，否则运维无法判断是漏设还是拼错。"""
    with pytest.raises(WeatherPolicyError) as exc:
        weather_policy.assert_provider_allowed("prd", "open-meteo")
    assert "APP_ENV='prd'" in str(exc.value)


def test_rejection_message_shows_empty_env_distinctly():
    """未设置与设置为空串都要能看出来，不能显示成 "APP_ENV=" 一句空话。"""
    with pytest.raises(WeatherPolicyError) as exc:
        weather_policy.assert_provider_allowed("", "open-meteo")
    assert "APP_ENV=''" in str(exc.value)


# ============ 判定函数本身 ============

def test_open_meteo_allowed_is_fail_closed():
    """默认必须是"不允许"。这一条是整道闸门的地基。"""
    assert weather_policy.open_meteo_allowed("") is False
    assert weather_policy.open_meteo_allowed(None) is False
    assert weather_policy.open_meteo_allowed("dev") is True
    assert weather_policy.open_meteo_allowed("DEV") is True
    assert weather_policy.open_meteo_allowed(" dev ") is True


def test_multi_env_all_must_be_in_allowlist():
    """多环境混写时必须**全部**在白名单内。

    只要「任一匹配就放行」，`APP_ENV=dev,prod` 就是一条干净的绕过路径——
    而生产机上多写一个逗号比少写一个环境值容易得多。
    这段判定与 Java 侧的 activeProfiles 逐条对齐。
    """
    assert weather_policy.open_meteo_allowed("dev,test") is True
    assert weather_policy.open_meteo_allowed("dev, prod") is False
    assert weather_policy.open_meteo_allowed("metrics,prod") is False
    with pytest.raises(WeatherPolicyError):
        weather_policy.assert_provider_allowed("dev,prod", "open-meteo")


def test_canonical_provider_keeps_unknown_values():
    """未知取值原样返回，不能在这里"猜"成某个已知源。

    猜的后果是：把 `qweather-v2` 猜成 `qweather`，于是我们以为在用它，
    实际配置里是个不存在的名字。未知就该是未知。
    """
    assert weather_policy.canonical_provider("QWeather") == "qweather"
    assert weather_policy.canonical_provider("open_meteo") == "open-meteo"
    assert weather_policy.canonical_provider("qweather-v2") == "qweather-v2"
    assert weather_policy.canonical_provider(None) == ""


# ============ 与真实配置加载串联（不是只测纯函数）============

def test_settings_load_blocks_prod_open_meteo(monkeypatch):
    """走真实的 Settings.load()，确认闸门确实接在配置加载链上。

    只测纯函数是不够的：函数对、但没人调用，等于没有闸门。
    """
    from app.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WEATHER_PROVIDER", "open-meteo")
    with pytest.raises(WeatherPolicyError):
        Settings.load()


def test_settings_load_allows_dev_open_meteo(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("WEATHER_PROVIDER", "open-meteo")
    loaded = Settings.load()
    assert loaded.env == "dev"
    assert loaded.weather.provider == "open-meteo"


def test_settings_load_defaults_weather_off(monkeypatch):
    """默认 provider 必须是 none，且 env 为空——两者都不能有"默认放行"的倾向。"""
    from app.config import Settings

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("SPRING_PROFILES_ACTIVE", raising=False)
    monkeypatch.delenv("WEATHER_PROVIDER", raising=False)
    loaded = Settings.load()
    assert loaded.env == ""
    assert loaded.weather.provider == "none"


# ============ 与 Java 侧的环境旋钮对齐 ============

def test_env_falls_back_to_spring_profiles(monkeypatch):
    """APP_ENV 缺失时读 SPRING_PROFILES_ACTIVE。

    理由不是"少配一个方便"，而是防分叉：
    这个项目「当前是哪个环境」的正式旋钮只有 06 规范里那一个（Java 用）。
    如果 Python 另立一个必须单独设置的变量，迟早出现 Java 认为自己是 prod、
    Python 认为自己是 dev 的局面——两道闸门判定不一致，比只有一道更危险。
    """
    from app.config import Settings

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("SPRING_PROFILES_ACTIVE", "prod")
    assert Settings.load().env == "prod"


def test_app_env_wins_over_spring_profiles(monkeypatch):
    """两个都设时 APP_ENV 优先（显式更具体）。"""
    from app.config import Settings

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SPRING_PROFILES_ACTIVE", "prod")
    assert Settings.load().env == "test"


def test_spring_profiles_prod_also_blocks_open_meteo(monkeypatch):
    """只设 SPRING_PROFILES_ACTIVE=prod 也必须拦住——这才是真实部署形态：
    生产只声明一次环境，不该要求为 Python 再声明一次。"""
    from app.config import Settings

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("SPRING_PROFILES_ACTIVE", "prod")
    monkeypatch.setenv("WEATHER_PROVIDER", "open-meteo")
    with pytest.raises(WeatherPolicyError):
        Settings.load()
