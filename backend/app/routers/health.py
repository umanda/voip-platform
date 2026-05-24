import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_db, get_redis

router = APIRouter(tags=["health"])
_settings = get_settings()


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """
    ECS health check endpoint. Must return HTTP 200 for the task to be healthy.

    Checks database connectivity (SELECT 1) and Redis connectivity (PING).
    ECS replaces tasks whose health check fails — keep this endpoint fast.
    """
    db_status = "ok"
    redis_status = "ok"

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        await redis.ping()
    except Exception:
        redis_status = "error"

    overall = "healthy" if db_status == "ok" and redis_status == "ok" else "degraded"

    return {
        "status": overall,
        "components": {"database": db_status, "redis": redis_status},
        "version": _settings.app_version,
    }
