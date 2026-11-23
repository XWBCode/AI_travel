"""和风天气 provider（D-03：生产数据源）。

## 接口口径（逐条来自 dev.qweather.com，不采信第三方文章）

    GET {专属API Host}/weather/v1/daily/{lat}/{lon}?days=1-10&localTime=true&lang=zh
    Authorization: Bearer <JWT>

- **没有日期范围参数**。只接受 `days`（1–10 天，默认 7），返回的是**从今天开始**的连续天数。
  这和 Open-Meteo 的 `start_date/end_date` 有本质区别：我们能做的只是"多要几天再按日期裁"，
  不能"只要 12 月 3 日那天"。
- 响应根是 `metadata` + **`days`**（不是 `daily`）。每天**没有 date 字段**，
  唯一的时间锚是 `forecastStartTime`。
- 温度是 `{value, unit}` 对象；降水概率是 **0–1**；`wind.scale` 直接是蒲福风级；
  `daytime.condition.text` 直接是中文。

## 三个容易错的地方（都在这里处理掉）

1. **降水准换**：官方 `probability` 取值 `[0, 1]`，我们是 0–100 的百分数口径，必须 ×100。
   这里错了不会报错，只会让"降水概率 64%"显示成"1%"。
2. **按天切分**：必须 `localTime=true`，否则 `forecastStartTime` 是 UTC，
   东八区的 `2024-08-10T22:00Z` 会被算成 8 月 10 日——**整体错一天**。
3. **风级**：不要自己拿 m/s 算蒲福级，官方 `scale` 就是蒲福级。两套口径同时存在时，
   用户会看到同一天两个不同的"几级风"。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.tools.weather_jwt import JwtConfigError, QWeatherTokenProvider
from app.tools.weather_providers.base import (
    NormalizedDay,
    ProviderFailure,
    ProviderResult,
    celsius,
    num,
    num_from,
)
from app.tools.weather_reasons import (
    R_PROVIDER_DISABLED,
    R_UPSTREAM_ERROR,
    R_UPSTREAM_TIMEOUT,
)

logger = logging.getLogger("ai-travel.weather.qweather")

SOURCE = "QWEATHER"
# PM 指定文案；官方归因页要求「和风天气」或「QWeather」+ 超链 https://www.qweather.com，
# 链接由前端按这段文字配上（契约不加字段，见 D-03 §5）
ATTRIBUTION = "天气服务由和风天气驱动"

# 官方窗口：1–10 天
MAX_DAYS = 10

# 东八区固定偏移（中国无夏令时，与 Asia/Shanghai 完全等价）。
# 不用 ZoneInfo 是因为 Windows 不带 IANA 时区库，为一个时区引入 tzdata 不划算。
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _normalize_host(host: str) -> str:
    """API Host 是每个开发者的**专属**域名，官方要求从控制台复制。用错直接 403 INVALID HOST。"""
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ProviderFailure(R_PROVIDER_DISABLED, "天气服务未配置 API Host", hard=True)
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return "https://" + host


_TOKEN_CACHE: dict[str, QWeatherTokenProvider] = {}


def _token_provider(settings: Any, *, clock: Any = None) -> QWeatherTokenProvider:
    """按凭据指纹复用 token provider，避免每次请求都重新签名。"""
    pem = str(getattr(settings, "qweather_private_key_pem", "") or "")
    fingerprint = hashlib.sha256(
        "|".join(
            [
                str(getattr(settings, "qweather_developer_id", "") or ""),
                str(getattr(settings, "qweather_project_id", "") or ""),
                str(getattr(settings, "qweather_key_id", "") or ""),
                str(getattr(settings, "qweather_private_key_path", "") or ""),
                pem,
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    provider = _TOKEN_CACHE.get(fingerprint)
    if provider is None:
        provider = QWeatherTokenProvider.from_settings(settings, **({"clock": clock} if clock else {}))
        _TOKEN_CACHE[fingerprint] = provider
    return provider


def reset_token_cache() -> None:
    """仅用于测试。"""
    _TOKEN_CACHE.clear()


def _day_from_forecast_start(raw: Any) -> date | None:
    """`forecastStartTime` 是唯一的时间锚，转成本地日期。

    官方示例 `2024-08-10T22:00Z` 对应"8 月 11 日"这一天（+08:00 后是 8 月 11 日 06:00），
    所以**必须**先转本地时区再取 date。直接 `[:10]` 会整体错一天。
    """
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # 没带时区说明我们漏传了 localTime=true，此时不能猜，交给调用方按序号兜底
        return None
    return dt.astimezone(LOCAL_TZ).date()


def _map_dates(items: list[dict[str, Any]], today_local: date) -> list[date]:
    """把上游的 N 条预报映射成 N 个日期。

    规则：先按 `forecastStartTime` 算，再和「今天 + 序号」交叉校验。
    - 两者一致 → 用序号（连续、不会因为个别字段缺失出现空洞）；
    - 任何一条不一致 → 记 warning，并**改用 `forecastStartTime`**（上游口径变了时以数据为准）。

    这条交叉校验是刻意加的：官方接口没有 `date` 字段，日期只能推。
    如果哪天上游把"天"的起点从 06:00 挪到 22:00，"序号推法"会安静地错一天，
    而这种错误在界面上看起来完全正常。
    """
    expected = [today_local + timedelta(days=i) for i in range(len(items))]
    derived = [_day_from_forecast_start(item.get("forecastStartTime")) for item in items]

    mismatched = [
        (i, d, expected[i]) for i, d in enumerate(derived) if d is not None and d != expected[i]
    ]
    if mismatched:
        first_index, got, want = mismatched[0]
        logger.warning(
            "和风天气日期口径与预期不一致 index=%s derived=%s expected=%s，改用 forecastStartTime",
            first_index, got, want,
        )
        return [d or expected[i] for i, d in enumerate(derived)]
    return expected


def _pick_period(item: dict[str, Any], period: str) -> dict[str, Any]:
    """取白天/夜间预报块。PM 决定只取白天（`daytime`）。"""
    block = item.get(period)
    return block if isinstance(block, dict) else {}


def _wind(block: dict[str, Any]) -> tuple[int | None, float | None]:
    """返回 (蒲福风级, 风速 m/s)。风级优先用官方 `scale`，不自己换算。"""
    wind = block.get("wind")
    if not isinstance(wind, dict):
        return None, None
    scale_raw = num(wind.get("scale"))
    scale = int(round(scale_raw)) if scale_raw is not None else None

    speed_obj = wind.get("speed")
    speed = num_from(speed_obj)
    if speed is not None:
        unit = str((speed_obj or {}).get("unit") or "").strip().lower()
        if unit in ("km/h", "kph"):
            speed = round(speed / 3.6, 2)
        elif unit not in ("m/s", ""):
            # 未知单位不猜，但风级仍然可用
            speed = None
    return scale, speed


def _probability(block: dict[str, Any]) -> float | None:
    """降水概率：官方 `[0, 1]` → 我们 0–100。

    同时兼容上游某天真的返回 0–100 的情况（>1 视为已经是百分数），
    这样即使口径变了一次，也不会把 64% 显示成 1%。
    """
    raw = num((block.get("precipitation") or {}).get("probability"))
    if raw is None:
        return None
    if raw < 0:
        return None
    if raw <= 1.0:
        return round(raw * 100)
    return round(min(raw, 100.0))


def normalize(payload: dict[str, Any], *, today_local: date) -> list[NormalizedDay]:
    """把上游响应整段转成 `NormalizedDay` 列表（不裁剪日期，裁剪由编排层做）。"""
    items = payload.get("days") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        # 上游 200 但没有 days：结构变了或该地无数据，都算上游异常，不能当成"没天气"
        keys = list(payload) if isinstance(payload, dict) else type(payload).__name__
        logger.warning("和风天气响应缺少 days 字段 keys=%s", keys)
        raise ProviderFailure(R_UPSTREAM_ERROR, "天气数据格式异常")

    dates = _map_dates(items, today_local)
    out: list[NormalizedDay] = []
    for item, day_date in zip(items, dates):
        day_block = _pick_period(item, "daytime")
        scale, speed = _wind(day_block)
        out.append(
            NormalizedDay(
                date=day_date,
                # 只用白天天气现象：这是"这天出门大概什么天气"最常用的判断（PM 决定）
                weather_text=(day_block.get("condition") or {}).get("text") or None,
                # 温度取整日口径（top-level），白天块里的是"白天时段"的最高/最低，不是全天
                temp_max=celsius(item.get("temperatureMax")),
                temp_min=celsius(item.get("temperatureMin")),
                temp_mean=celsius(item.get("temperatureAvg")),
                precip_prob=_probability(day_block),
                wind_scale=scale,
                wind_speed_ms=speed,
            )
        )
    return out


async def fetch(
    *,
    lat: float,
    lon: float,
    start: date,
    end: date,
    timeout_seconds: float = 3.0,
    connect_timeout_seconds: float = 2.0,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    settings: Any = None,
    now: datetime | None = None,
    token_provider: QWeatherTokenProvider | None = None,
    clock: Any = None,
) -> ProviderResult:
    """查询 [start, end]。注意上游只能"从今天开始 N 天"，所以内部先取够天数再裁。"""
    if settings is None:
        raise ProviderFailure(R_PROVIDER_DISABLED, "天气服务未配置", hard=True)

    host = _normalize_host(str(getattr(settings, "qweather_api_host", "") or ""))
    now = now or datetime.now(LOCAL_TZ)
    today_local = now.astimezone(LOCAL_TZ).date()

    if end > today_local + timedelta(days=MAX_DAYS - 1):
        # 编排层已经把窗口裁到 10 天以内；这里再断言一次，防止将来有人放宽窗口却没改 provider
        raise ProviderFailure(R_UPSTREAM_ERROR, "请求超出预报窗口")

    # 上游从"今天"开始给，要给到 end，至少需要 (end - today + 1) 天
    days = (end - today_local).days + 1
    days = max(1, min(MAX_DAYS, days))

    try:
        provider = token_provider or _token_provider(settings, clock=clock)
        token = provider.token()
    except JwtConfigError as e:
        # 凭据缺失/私钥不可读：不是上游的问题，重试不会好，直接判 hard
        logger.error("和风天气凭据配置错误：%s", e)
        raise ProviderFailure(R_PROVIDER_DISABLED, "天气服务未配置凭据", hard=True) from None

    url = f"{host}/weather/v1/daily/{lat:.2f}/{lon:.2f}"
    params = {"days": days, "localTime": "true", "lang": "zh"}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
            transport=transport,
        )
    try:
        payload, status = await _get(client, url, params, token)
        if status == 401:
            # token 过期/时钟偏差：换一次新 token 再试一次。401 是唯一值得重试的错误
            provider.invalidate()
            try:
                payload, status = await _get(client, url, params, provider.token())
            except JwtConfigError:
                raise ProviderFailure(R_PROVIDER_DISABLED, "天气服务未配置凭据", hard=True) from None
            if status == 401:
                raise ProviderFailure(R_UPSTREAM_ERROR, hard=True)
    finally:
        if owns_client:
            await client.aclose()

    return ProviderResult(source=SOURCE, attribution=ATTRIBUTION, days=normalize(payload, today_local=today_local))


async def _get(
    client: httpx.AsyncClient, url: str, params: dict[str, Any], token: str
) -> tuple[dict[str, Any], int]:
    """发一次请求，把 HTTP 状态收敛成 `ProviderFailure`（只有 401 原样返回给调用方重试）。"""
    try:
        resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException as e:
        raise ProviderFailure(R_UPSTREAM_TIMEOUT) from e
    except httpx.HTTPError as e:
        raise ProviderFailure(R_UPSTREAM_ERROR) from e

    status = resp.status_code
    if status == 200:
        try:
            payload = resp.json()
        except ValueError as e:
            raise ProviderFailure(R_UPSTREAM_ERROR, "天气数据格式异常") from e
        if not isinstance(payload, dict):
            raise ProviderFailure(R_UPSTREAM_ERROR, "天气数据格式异常")
        return payload, 200

    detail = _problem_detail(resp)
    logger.warning("和风天气 HTTP %s %s", status, detail)
    if status == 401:
        return {}, 401
    if status in (403, 429):
        # NO CREDIT / INVALID HOST / ACCOUNT SUSPENSION / QPM 超限：
        # 继续请求只会加速"被判定为攻击"，一次都不重试
        raise ProviderFailure(R_UPSTREAM_ERROR, hard=True)
    if status in (400, 404, 405):
        # 我们的请求构造是确定性的，重复同一个错请求没有意义
        raise ProviderFailure(R_UPSTREAM_ERROR, hard=True)
    raise ProviderFailure(R_UPSTREAM_ERROR)


def _problem_detail(resp: httpx.Response) -> str:
    """提取 `application/problem+json` 里的 title/detail，只用于日志（不外泄给用户）。"""
    try:
        body = resp.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if not isinstance(error, dict):
        return ""
    return " ".join(str(error.get(key) or "") for key in ("type", "title")).strip()

# 供编排层做窗口断言用
WINDOW_DAYS = MAX_DAYS
