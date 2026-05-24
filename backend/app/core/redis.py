import redis.asyncio as aioredis

from app.config import get_settings

_settings = get_settings()
_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """
    Return the shared Redis connection pool (singleton).

    Uses hiredis for faster response parsing where available.
    max_connections=50 handles concurrent call volume.
    """
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            _settings.redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return _pool


async def close_redis() -> None:
    """Close the Redis connection pool on app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
