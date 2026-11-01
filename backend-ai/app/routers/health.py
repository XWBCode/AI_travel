from fastapi import APIRouter, Request

from app import __version__
from app.config import settings

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health(request: Request):
    """健康检查。

    只返回非敏感信息：不返回 api_key、不返回 base_url 的鉴权部分。
    """
    provider = request.app.state.provider
    return {
        "code": 0,
        "data": {
            "service": settings.app_name,
            "version": __version__,
            "status": "UP",
            "llm": provider.describe(),
        },
    }
