"""天气查询端点（M3-1）。

    GET /v1/weather?city=成都&start_date=2026-11-24&days=3

只对 Java 服务开放，不直接给前端：鉴权、计划归属、缓存、降级文案都在 Java。
本端点的唯一职责是「调外部 + 归一化成 FactField」，因此它不认识 planId、不落库。

**失败也是一律 200**，用 `ok=false` + `reason` 表达——理由同设计稿 §4.1：
拿不到天气不是请求错误，用 4xx/5xx 会污染错误率、也会让人误以为服务坏了。
只有参数本身非法（`days` 非正整数）交给 FastAPI 的 422。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request

from app.config import settings
from app.tools.weather import R_PROVIDER_DISABLED, TZ, fetch_forecast

logger = logging.getLogger("ai-travel.weather")

router = APIRouter(prefix="/v1", tags=["weather"])


@router.get("/weather")
async def get_weather(
    request: Request,
    city: str | None = Query(default=None, description="城市名，可以是「成都」「大理」「云南」"),
    start_date: str | None = Query(default=None, description="出发日期 YYYY-MM-DD，为空表示计划没有日期"),
    days: int = Query(default=1, ge=1, le=16, description="需要几天，上限等于预报窗口"),
):
    trace_id = request.headers.get("X-Trace-Id", "")
    weather_settings = settings.weather

    if not weather_settings.enabled:
        # 配置层就关掉时连上游都不碰，返回原因让 Java 转成 PROVIDER_DISABLED
        logger.info("weather disabled by config trace=%s", trace_id)
        return {
            "ok": False,
            "reason": R_PROVIDER_DISABLED,
            "message": "天气功能未开启",
            "traceId": trace_id,
        }

    result = await fetch_forecast(
        city,
        start_date,
        days,
        provider=weather_settings.provider,
        timeout_seconds=weather_settings.timeout_seconds,
        connect_timeout_seconds=weather_settings.connect_timeout_seconds,
        now=datetime.now(TZ),
    )
    result["traceId"] = trace_id
    logger.info(
        "weather trace=%s city=%s start=%s days=%s ok=%s reason=%s",
        trace_id, city, start_date, days, result.get("ok"), result.get("reason"),
    )
    return result
