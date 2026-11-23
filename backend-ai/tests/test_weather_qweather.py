"""和风天气 provider 单测（D-03 实施）。

全部离线。真实 API 的联调脚本在 `scripts/verify_weather_qweather.py`（需凭据，不在 CI 里跑）。

## 这个文件重点防的是"看起来对但对不上"

换数据源最容易出的不是崩溃，是**安静地差一点**：

- 降水概率上游给 0.64，我们口径是 64。少乘 100 不报错，界面显示"降水概率 1%"；
- `forecastStartTime` 是 UTC，`2024-08-10T22:00Z` 其实是东八区的 8 月 11 日。直接切前 10 个字符
  会整体错一天；
- 温度是 `{value, unit}` 对象，unit 若是 `°F` 而我们照抄数字，会显示一个"离谱但不报错"的温度。

所以下面每条都有对应的断言，而不是只测"能不能跑通"。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from jsonschema import Draft202012Validator

from app.config import WeatherSettings
from app.plan.contract import load_schema
from app.tools import weather, weather_circuit
from app.tools.weather import R_PROVIDER_DISABLED, R_UPSTREAM_ERROR, R_UPSTREAM_TIMEOUT, fetch_forecast
from app.tools.weather_jwt import JwtConfigError, QWeatherTokenProvider
from app.tools.weather_providers import qweather
from app.tools.weather_providers.base import ProviderFailure

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
# 2026-11-23 22:40 —— 刻意选在深夜，跨天失效与"明天起 3 天"的表现都能覆盖到
NOW = datetime(2026, 11, 23, 22, 40, tzinfo=TZ)
TODAY = date(2026, 11, 23)


def _keypair() -> tuple[str, str]:
    """生成一对临时 Ed25519 密钥（PEM）。只用一次，不落盘。"""
    key = ed25519.Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


PRIVATE_PEM, PUBLIC_PEM = _keypair()


def _settings(**overrides) -> WeatherSettings:
    base = dict(
        provider="qweather",
        qweather_api_host="abcxyz.qweatherapi.com",
        qweather_developer_id="Q12345ABCD",
        qweather_project_id="PROJECT123",
        qweather_key_id="KEYID123",
        qweather_private_key_pem=PRIVATE_PEM,
    )
    base.update(overrides)
    return WeatherSettings(**base)


def _token(settings: WeatherSettings | None = None, clock=None) -> QWeatherTokenProvider:
    return QWeatherTokenProvider.from_settings(settings or _settings(), **({"clock": clock} if clock else {}))


def _day_payload(
    n: int = 3,
    *,
    start: date = date(2026, 11, 24),
    prob: float = 0.64,
    temp_unit: str = "°C",
    text: str = "小雨",
    scale: int | None = 2,
    include_days: bool = True,
) -> dict:
    """造一份形状与官方示例同构的响应。

    日期一律用「前一天 22:00Z」这种写法——官方示例就是这样，也正是"错一天"的来源。
    """
    items = []
    for i in range(n):
        # 官方示例里"某一天"的 forecastStartTime 是**前一天 22:00Z**（= 本地当天 06:00）。
        # 这里照抄这个偏移，才能测出"切字符串会错一天"这个坑。
        local_six_am = datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=TZ) + timedelta(hours=6)
        utc = local_six_am.astimezone(timezone.utc)
        items.append(
            {
                "forecastStartTime": utc.strftime("%Y-%m-%dT%H:%MZ"),
                "forecastEndTime": (utc + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ"),
                "temperatureMax": {"value": 16.0, "unit": temp_unit},
                "temperatureMin": {"value": 12.0, "unit": temp_unit},
                "temperatureAvg": {"value": 14.0, "unit": temp_unit},
                "uvIndexMax": 6,
                "daytime": {
                    "condition": {"text": text, "code": "305"},
                    "wind": {"speed": {"value": 5.0, "unit": "m/s"}, "scale": scale},
                    "precipitation": {"amount": {"value": 0.75, "unit": "mm"}, "probability": prob},
                },
                "nighttime": {
                    "condition": {"text": "晴间多云", "code": "103"},
                    "wind": {"speed": {"value": 2.33, "unit": "m/s"}, "scale": 2},
                    "precipitation": {"amount": {"value": 0, "unit": "mm"}, "probability": 0},
                },
            }
        )
    payload = {"metadata": {"tag": "x", "attributions": ["https://developer.qweather.com/attribution.html"]}}
    if include_days:
        payload["days"] = items
    return payload


def _transport(responses, *, calls: list | None = None):
    """按顺序返回预设响应；`calls` 记录每次请求（方法、URL、params、Authorization）。"""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(
                {
                    "url": str(request.url),
                    "params": dict(request.url.params),
                    "auth": request.headers.get("Authorization", ""),
                    "accept_encoding": request.headers.get("Accept-Encoding", ""),
                }
            )
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        if status >= 400 and isinstance(body, dict) and "error" not in body:
            body = {"error": {"status": status, "type": "https://x", "title": "err", "detail": "d"}}
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _isolate():
    """熔断器与 token 缓存都是进程级状态，逐用例清空，避免"靠顺序绿"。"""
    weather_circuit.reset_all()
    qweather.reset_token_cache()
    yield
    weather_circuit.reset_all()
    qweather.reset_token_cache()


def _fetch(payload=None, transport=None, **kwargs):
    settings = kwargs.pop("settings", _settings())
    start = kwargs.pop("start", date(2026, 11, 24))
    days = kwargs.pop("days", 3)
    if transport is None:
        transport = _transport([(200, payload if payload is not None else _day_payload(days, start=start))])
    return asyncio.run(
        fetch_forecast(
            kwargs.pop("city", "成都"),
            kwargs.pop("start_date", start.isoformat()),
            days,
            provider=kwargs.pop("provider", settings.provider),
            transport=transport,
            settings=settings,
            now=kwargs.pop("now", NOW),
            **kwargs,
        )
    )


# ---------------------------------------------------------------- JWT


def _decode(token: str) -> tuple[dict, dict, bytes]:
    import base64

    head, body, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    return (
        json.loads(base64.urlsafe_b64decode(pad(head))),
        json.loads(base64.urlsafe_b64decode(pad(body))),
        base64.urlsafe_b64decode(pad(sig)),
    )


def test_jwt_carries_exactly_the_official_claims():
    """官方口径：header{alg:EdDSA, kid:凭据ID}，payload{iss:开发者ID, sub:项目ID, iat:now-30, exp}。

    断言"逐字"，而不是"大致有这几个 key"：iss/sub 填反了接口只会回 401，
    而 401 的响应体官方明确不给原因，猜的成本很高。
    """
    header, payload, signature = _decode(_token().token())
    assert header["alg"] == "EdDSA"
    assert header["kid"] == "KEYID123"
    assert "typ" not in header or header["typ"] == "JWT"  # 保留字段，带了就必须是 JWT

    assert payload["iss"] == "Q12345ABCD"
    assert payload["sub"] == "PROJECT123"
    assert payload["exp"] - payload["iat"] == 900
    assert payload["iat"] <= int(time.time()) - 30 + 5
    assert "aud" not in payload and "nbf" not in payload  # 保留字段不带
    assert len(signature) == 64  # Ed25519 签名固定 64 字节


def test_jwt_signature_verifies_with_the_public_key():
    """签名必须能被公钥验证——否则"看起来是个 JWT"但上游会 401。"""
    token_provider = _token()
    _, _, signature = _decode(token_provider.token())
    head_body = token_provider.token().rsplit(".", 1)[0].encode()
    ed25519.Ed25519PublicKey.from_public_bytes(
        __import__("cryptography").hazmat.primitives.serialization.load_pem_public_key(PUBLIC_PEM.encode()).public_bytes_raw()
    ).verify(signature, head_body)


def test_jwt_is_cached_and_refreshed_before_expiry():
    """每次请求都重签会白白消耗 CPU；缓存到快过期才换。用假时钟验证，不真睡。"""
    fake = {"now": 1_700_000_000.0}
    provider = _token(clock=lambda: fake["now"])
    first = provider.token()
    assert provider.token() == first, "同一时间窗内必须复用"

    fake["now"] += 900 - 120 + 1  # 越过提前刷新线
    assert provider.token() != first


def test_jwt_invalidate_forces_new_token():
    """上游 401 后必须能强制换新，否则会一直拿同一个坏 token 重试到被风控。"""
    fake = {"now": 1_700_000_000.0}
    provider = _token(clock=lambda: fake["now"])
    first = provider.token()
    provider.invalidate()
    fake["now"] += 1  # Ed25519 是确定性签名：iat 不变时同一个 payload 会签出同一个 token
    assert provider.token() != first


def test_jwt_config_errors_are_explicit():
    with pytest.raises(JwtConfigError):
        QWeatherTokenProvider(developer_id="", project_id="P", key_id="K", private_key_pem=PRIVATE_PEM)
    with pytest.raises(JwtConfigError):
        QWeatherTokenProvider(developer_id="Q", project_id="P", key_id="K", private_key_pem="not-a-pem")
    with pytest.raises(JwtConfigError):
        # 官方上限 24 小时
        QWeatherTokenProvider(developer_id="Q", project_id="P", key_id="K", private_key_pem=PRIVATE_PEM, ttl_seconds=86401)


def test_missing_credentials_degrades_without_calling_upstream():
    """凭据没配齐时一次请求都不能发：这类错误重试只会加速风控。"""
    calls: list = []
    result = _fetch(
        settings=_settings(qweather_key_id=""),
        transport=_transport([(200, _day_payload())], calls=calls),
    )
    assert result["ok"] is False
    assert result["reason"] == R_PROVIDER_DISABLED
    assert calls == []


# ---------------------------------------------------------------- 归一化


def test_precipitation_probability_is_converted_to_percent():
    """官方 `probability` 是 [0,1]，我们是 0–100。少乘 100 就是"64% 显示成 1%"。"""
    result = _fetch(payload=_day_payload(1, prob=0.64))
    assert result["days"][0]["precipitation"]["value"] == 64
    assert result["days"][0]["precipitation"]["display"] == "降水概率 64%"

    zero = _fetch(payload=_day_payload(1, prob=0.0))
    # 0 是"真的 0%"，必须是 verified 的真值，而不是"没拿到"
    assert zero["days"][0]["precipitation"]["value"] == 0
    assert zero["days"][0]["precipitation"]["verified"] is True


def test_wind_scale_is_used_directly_not_recomputed():
    """官方 `scale` 就是蒲福风级。自己拿 m/s 再算一遍会得到两套口径。"""
    result = _fetch(payload=_day_payload(1, scale=5))
    assert result["days"][0]["wind"]["value"] == 5
    assert result["days"][0]["wind"]["display"] == "5级风"


def test_daytime_condition_text_is_used_verbatim():
    """只取白天（PM 决定）。夜间是"晴间多云"，如果取错 block 会显示成夜间天气。"""
    result = _fetch(payload=_day_payload(1, text="小雨"))
    assert result["days"][0]["weather"]["value"] == "小雨"
    assert result["days"][0]["weather"]["display"] == "小雨"
    assert result["days"][0]["weather"]["verified"] is True


def test_non_celsius_temperature_is_rejected_not_converted():
    """unit 不是 ℃ 就当没拿到。静默换算的问题不是算错，而是"算对了也看不出来"。"""
    result = _fetch(payload=_day_payload(1, temp_unit="°F"))
    temp = result["days"][0]["temperature"]
    assert temp["value"] is None
    assert temp["verified"] is False
    assert temp["source"] == "NONE"
    assert temp["display"] == "暂无数据"


def test_missing_days_key_is_upstream_error_not_empty_weather():
    result = _fetch(payload=_day_payload(1, include_days=False))
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_ERROR


def test_empty_days_list_is_upstream_error():
    with pytest.raises(ProviderFailure):
        qweather.normalize({"days": []}, today_local=TODAY)


# ---------------------------------------------------------------- 日期口径


def test_utc_timestamp_maps_to_local_date_not_utc_date():
    """`2024-08-10T22:00Z` 是东八区的 8 月 11 日。切字符串会整体错一天。"""
    items = [{"forecastStartTime": "2026-11-22T22:00Z"}]  # = 本地 11-23 06:00
    assert qweather._map_dates(items, TODAY) == [date(2026, 11, 23)]


def test_three_consecutive_days_come_back_consecutive():
    """上游只会"从今天开始 N 天"，所以要 24 号起 3 天就得先多要一天再裁。"""
    payload = _day_payload(4, start=TODAY)  # 11-23 ~ 11-26
    result = _fetch(payload=payload, start=date(2026, 11, 24), days=3)
    assert [d["date"] for d in result["days"]] == ["2026-11-24", "2026-11-25", "2026-11-26"]
    assert [d["dayIndex"] for d in result["days"]] == [1, 2, 3]


def test_date_comes_from_upstream_when_it_disagrees_with_position():
    """上游口径一旦变化（比如"天"的起点挪了），以数据为准并留 warning。

    这条是防"我们按序号推日期，上游其实换了口径"——那种错在界面上看起来完全正常。
    """
    items = [{"forecastStartTime": "2026-11-24T02:00Z"}, {"forecastStartTime": "2026-11-25T02:00Z"}]
    # 期望 11-23/11-24，实际是 11-24/11-25 → 必须采用上游
    assert qweather._map_dates(items, TODAY) == [date(2026, 11, 24), date(2026, 11, 25)]


def test_missing_time_anchor_falls_back_to_position():
    items = [{}, {}]
    assert qweather._map_dates(items, TODAY) == [TODAY, TODAY + timedelta(days=1)]


# ---------------------------------------------------------------- 请求构造


def test_request_targets_the_dedicated_host_with_days_and_local_time():
    """API Host 是开发者专属域名（用错 403 INVALID HOST），且必须 localTime=true。

    `days` 是 4 不是 3，这**不是 bug**：上游只支持"从今天开始 N 天"，我们要 24 号起的
    3 天，就只能要"今天起 4 天"再按日期裁掉今天。写进断言是为了防止有人"顺手优化"成 3。
    """
    calls: list = []
    _fetch(payload=_day_payload(3), transport=_transport([(200, _day_payload(3))], calls=calls))
    call = calls[0]
    assert call["url"].startswith("https://abcxyz.qweatherapi.com/weather/v1/daily/30.57/104.07")
    assert call["params"]["localTime"] == "true"
    assert call["params"]["days"] == "4"
    assert call["auth"].startswith("Bearer ey")


def test_host_with_scheme_is_accepted():
    result = _fetch(
        settings=_settings(qweather_api_host="https://abcxyz.qweatherapi.com/"),
        transport=_transport([(200, _day_payload(1))]),
        days=1,
    )
    assert result["ok"] is True


def test_405_style_bad_host_degrades_as_hard_failure():
    result = _fetch(transport=_transport([(403, {})]))
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_ERROR
    # 403 只打一次：继续请求会被判成攻击
    assert weather_circuit.breaker_for("qweather").state == weather_circuit.OPEN


# ---------------------------------------------------------------- 错误分型


@pytest.mark.parametrize("status", [403, 429])
def test_hard_errors_are_not_retried(status):
    calls: list = []
    result = _fetch(transport=_transport([(status, {})], calls=calls))
    assert result["ok"] is False
    assert len(calls) == 1, "403/429 重试只会加速被冻结账号"


def test_401_retries_once_with_a_fresh_token():
    """401 是唯一值得重试的：可能只是 token 过期或时钟偏差。只重试一次，且换新 token。

    用一个记录调用的假 token provider 来判断"到底有没有重签"。Ed25519 是确定性签名，
    同一秒内 payload 不变会签出**完全相同**的 token，所以拿真实 token 比字符串会误判。
    """

    class CountingToken:
        def __init__(self):
            self.issued = 0
            self.invalidated = 0

        def token(self):
            self.issued += 1
            return f"token-{self.issued}"

        def invalidate(self):
            self.invalidated += 1

    calls: list = []
    counting = CountingToken()
    result = _fetch(
        transport=_transport([(401, {}), (200, _day_payload(3))], calls=calls),
        token_provider=counting,
    )
    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[0]["auth"] == "Bearer token-1"
    assert calls[1]["auth"] == "Bearer token-2", "第二次必须用新签的 token"
    assert counting.invalidated == 1


def test_401_twice_is_a_hard_failure():
    calls: list = []
    result = _fetch(transport=_transport([(401, {}), (401, {})], calls=calls))
    assert result["ok"] is False
    assert len(calls) == 2
    assert weather_circuit.breaker_for("qweather").state == weather_circuit.OPEN


def test_timeout_is_classified_and_counts_toward_breaker():
    result = _fetch(transport=_transport([httpx.ReadTimeout("slow")]))
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_TIMEOUT


def test_500_is_soft_failure():
    result = _fetch(transport=_transport([(500, {})]))
    assert result["ok"] is False
    assert result["reason"] == R_UPSTREAM_ERROR
    # 单个 5xx 不该立刻停服
    assert weather_circuit.breaker_for("qweather").state == weather_circuit.CLOSED


# ---------------------------------------------------------------- 熔断


def test_breaker_opens_after_threshold_and_stops_calling_upstream():
    """官方明确写了持续错误请求可能被冻结账号，所以熔断必须真的拦住请求。"""
    calls: list = []
    settings = _settings()
    transport = _transport([(500, {})], calls=calls)
    for _ in range(3):
        assert _fetch(transport=transport, settings=settings)["ok"] is False
    assert len(calls) == 3
    assert weather_circuit.breaker_for("qweather").state == weather_circuit.OPEN

    blocked = _fetch(transport=transport, settings=settings)
    assert blocked["ok"] is False
    assert blocked["reason"] == R_UPSTREAM_ERROR
    assert len(calls) == 3, "熔断期间一个请求都不许发"


def test_hard_failure_opens_breaker_immediately():
    _fetch(transport=_transport([(429, {})]))
    assert weather_circuit.breaker_for("qweather").state == weather_circuit.OPEN


def test_breaker_recovers_after_cooldown_with_single_trial():
    breaker = weather_circuit.breaker_for("qweather", failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure(1000.0)
    assert breaker.allow(1030.0) is False
    assert breaker.allow(1061.0) is True
    assert breaker.state == weather_circuit.HALF_OPEN
    breaker.record_success()
    assert breaker.state == weather_circuit.CLOSED


def test_half_open_failure_doubles_the_cooldown():
    breaker = weather_circuit.CircuitBreaker(name="x", failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure(0.0)
    assert breaker.allow(61.0) is True  # 半开
    breaker.record_failure(61.0)
    assert breaker.allow(61.0 + 59) is False, "冷却应加倍到 120s"


# ---------------------------------------------------------------- 缓存过期（跨天）


def test_expiry_is_capped_at_next_midnight():
    """23:00 缓存"明天起 3 天"，次日不失效就会整体错一天（官方缓存文档点名的坑）。"""
    late = datetime(2026, 11, 23, 23, 50, tzinfo=TZ)
    assert weather.expiry_of(late, 60) == datetime(2026, 11, 24, 0, 10, tzinfo=TZ)


def test_expiry_uses_ttl_when_far_from_midnight():
    noon = datetime(2026, 11, 23, 12, 0, tzinfo=TZ)
    assert weather.expiry_of(noon, 60) == datetime(2026, 11, 23, 13, 0, tzinfo=TZ)


def test_response_expiry_never_crosses_midnight():
    late = datetime(2026, 11, 23, 23, 30, tzinfo=TZ)
    result = _fetch(payload=_day_payload(1), days=1, now=late)
    assert result["expiresAt"] == "2026-11-24T00:10:00+08:00"
    assert result["ttlMinutes"] == 60


# ---------------------------------------------------------------- 窗口


def test_window_is_ten_days_after_d03():
    """和风天气官方上限 10 天；前后端必须用同一个数字。"""
    assert weather.FORECAST_WINDOW_DAYS == 10
    assert qweather.MAX_DAYS == 10
    start, end = weather.forecast_window(NOW)
    assert (end - start).days == 9


def test_request_beyond_window_is_clipped_not_forwarded():
    far = TODAY + timedelta(days=12)
    calls: list = []
    result = _fetch(
        start=far,
        start_date=far.isoformat(),
        transport=_transport([(200, _day_payload(3))], calls=calls),
    )
    assert result["ok"] is False
    assert result["reason"] == "DATE_OUT_OF_RANGE"
    assert calls == []


# ---------------------------------------------------------------- 双 provider 契约一致性


def _fact_field_validator() -> Draft202012Validator:
    full = load_schema("travel_plan_v1.json")
    return Draft202012Validator({"$defs": full["$defs"], **full["$defs"]["factFieldNumber"]})


def test_both_providers_produce_contract_valid_fact_fields():
    """两个 provider 的出参必须都能过同一份契约。

    这是"契约与数据源解耦"的机器证明：不是看代码像不像，而是看两条真实路径的产物
    能不能被同一个校验器接受。
    """
    validator = _fact_field_validator()
    qweather_result = _fetch(payload=_day_payload(2))
    open_meteo_payload = {
        "daily": {
            "time": ["2026-11-24", "2026-11-25"],
            "weather_code": [2, 61],
            "temperature_2m_max": [16.0, 15.0],
            "temperature_2m_min": [12.0, 11.0],
            "temperature_2m_mean": [14.0, 13.0],
            "precipitation_probability_max": [6, 80],
            "wind_speed_10m_max": [18.0, 22.0],
        }
    }
    open_meteo_result = _fetch(
        payload=open_meteo_payload, provider="open-meteo", days=2
    )

    for result in (qweather_result, open_meteo_result):
        assert result["ok"] is True
        for day in result["days"]:
            for key in ("temperature", "precipitation", "wind"):
                errors = list(validator.iter_errors(day[key]))
                assert not errors, f"{result['source']} 的 {key} 不合契约: {[e.message for e in errors]}"


def test_both_providers_share_the_same_display_semantics():
    """同一个"12–16℃ / 降水 6% / 3级风"，两个 provider 必须显示成同一句话。

    如果换源后文案变了（比如"3级风"变成"5.0m/s"），说明归一化没统一，用户会看到"界面也跟着换源了"。
    """
    qwea = _fetch(payload=_day_payload(1, prob=0.06, scale=3))
    om = _fetch(
        payload={
            "daily": {
                "time": ["2026-11-24"],
                "weather_code": [2],
                "temperature_2m_max": [16.0],
                "temperature_2m_min": [12.0],
                "temperature_2m_mean": [14.0],
                "precipitation_probability_max": [6],
                "wind_speed_10m_max": [18.0],
            }
        },
        provider="open-meteo",
        days=1,
    )
    for key in ("temperature", "precipitation", "wind"):
        assert qwea["days"][0][key]["display"] == om["days"][0][key]["display"], key


def test_source_and_attribution_differ_by_provider():
    """`source` 变了，但**结构不变**；归因按各自许可要求给。"""
    assert _fetch(payload=_day_payload(1), days=1)["source"] == "QWEATHER"
    assert _fetch(payload=_day_payload(1), days=1)["attribution"] == "天气服务由和风天气驱动"


def test_unknown_provider_value_is_disabled_not_crash():
    result = _fetch(provider="qweather-v2", transport=_transport([(200, _day_payload(1))]))
    assert result["ok"] is False
    assert result["reason"] == R_PROVIDER_DISABLED
