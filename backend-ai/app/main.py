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
from app.routers import chat, health, plan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("ai-travel")


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(plan.router)

    @app.exception_handler(LLMError)
    async def handle_llm_error(request: Request, exc: LLMError):
        logger.error("LLM 调用失败 path=%s err=%s", request.url.path, exc)
        return JSONResponse(status_code=502, content={"code": 50201, "message": str(exc)})

    return app


app = create_app()
