"""应用工厂：注册路由、统一异常、生命周期管理。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.llm.provider import LLMError, build_provider
from app.plan.contract import DRAFT_SCHEMA_FILE, load_schema
from app.routers import chat, health, plan, weather
from app.tools import service_guard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("ai-travel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 环境与数据源必须在启动日志里可见：数据源合规闸门是按 env 判定的，
    # 如果日志里看不到判定依据，"为什么这台机器起不来"就得靠猜。
    logger.info(
        "运行环境: %s%s",
        settings.env or "未声明（APP_ENV 与 SPRING_PROFILES_ACTIVE 均为空）",
        "" if settings.weather.enabled else "，天气数据源未启用",
    )
    if settings.weather.enabled:
        logger.info("天气数据源: %s", settings.weather.provider)
    _log_guard_mode()
    # 启动时构建 Provider，配置错误直接启动失败，而不是等第一个用户请求才报错
    app.state.provider = build_provider(settings.llm)
    logger.info("LLM Provider 已就绪: %s", app.state.provider.describe())
    if settings.llm.is_mock:
        logger.warning("当前处于 MOCK 模式，未调用真实大模型，请勿用于演示或验收")
    # 契约是硬依赖：文件缺失或损坏时应当启动失败，而不是等第一个生成请求才报错
    schema = load_schema(DRAFT_SCHEMA_FILE)
    logger.info("TravelPlan 契约已加载: %s (schemaVersion=%s)", schema.get("$id"), "1.0")
    yield
    logger.info("AI 服务已关闭")


def _log_guard_mode() -> None:
    """把 `/v1/*` 的鉴权模式写进启动日志。

    "这台机器到底有没有鉴权"必须能在日志里一眼看到，否则排障只能靠猜配置。
    """
    if settings.internal_secret:
        logger.info("/v1/* 鉴权: 强制令牌（AI_INTERNAL_SECRET 已配置）")
    elif (settings.env or "").strip().lower() in ("prod", "production"):
        logger.error(
            "/v1/* 鉴权: APP_ENV=prod 但未配 AI_INTERNAL_SECRET —— "
            "所有内部调用将被拒绝（40301），这是预期行为，先配密钥"
        )
    else:
        logger.warning(
            "/v1/* 鉴权: 未配密钥，仅放行回环来源（dev 模式）。"
            "非回环来源一律 403；生产必须先配 AI_INTERNAL_SECRET"
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Travel Assistant - AI Service",
        version=__version__,
        description="AI 能力服务层：LLM 调用、Prompt、Agent 编排、RAG。不持有业务数据。",
        lifespan=lifespan,
    )

    # 仅允许 Java 服务与本机调试访问；生产由 Nginx 收敛，不对外开放本端口
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def guard_internal_calls(request: Request, call_next):
        """`/v1/*` 接口级鉴权（TD-02）：只有持有内部令牌的 Java 服务能调。

        判定表与取舍见 `app/tools/service_guard.py`。这里只负责接线，不藏判断逻辑——
        判断逻辑要能单测，所以放在纯函数里。
        """
        # CORS 预检不带自定义头，拦它等于让浏览器端永远拿不到响应。
        if request.method != "OPTIONS" and service_guard.is_protected(request.url.path):
            denied = service_guard.decide(
                request.headers,
                secret=settings.internal_secret,
                env=settings.env,
                client_host=request.client.host if request.client else None,
            )
            if denied is not None:
                logger.warning(
                    "内部接口被拒 path=%s from=%s code=%s",
                    request.url.path,
                    request.client.host if request.client else "?",
                    denied["code"],
                )
                return JSONResponse(status_code=403, content=denied)
        return await call_next(request)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(plan.router)
    app.include_router(weather.router)

    @app.exception_handler(LLMError)
    async def handle_llm_error(request: Request, exc: LLMError):
        logger.error("LLM 调用失败 path=%s err=%s", request.url.path, exc)
        return JSONResponse(status_code=502, content={"code": 50201, "message": str(exc)})

    return app


app = create_app()
