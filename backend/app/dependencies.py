from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis as _get_redis


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an async SQLAlchemy session, close on exit."""
    async with AsyncSessionLocal() as session:
        yield session


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency: return the shared Redis connection pool."""
    return await _get_redis()
