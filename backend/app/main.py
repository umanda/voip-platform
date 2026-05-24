from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import close_redis
from app.routers import billing, call, health

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging(debug=_settings.debug)
    yield
    await close_redis()


app = FastAPI(
    title="VoIP Platform API",
    version=_settings.app_version,
    description=(
        "FastAPI backend replacing PHP Sentinel. "
        "Serves FreeSWITCH Lua dialplan via internal HTTP calls. "
        "All endpoints must respond within 500ms p99 (R-SIP-01 requires 2000ms max)."
    ),
    lifespan=lifespan,
    docs_url="/docs" if _settings.debug else None,
    redoc_url=None,
)

app.include_router(health.router)
app.include_router(call.router)
app.include_router(billing.router)
