from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_db, get_redis
from app.main import app
from app.models.db.credits_customers import CreditsCustomer
from app.models.db.site_ivr_numbers import SiteIvrNumber


@pytest.fixture
def mock_redis() -> AsyncMock:
    """
    Mock aioredis.Redis with typical return values.

    eval returns 100_000 = 1.00000 credit unit (sufficient for default test calls).
    Override per-test when testing insufficient credit or deduction failure paths.
    """
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.hset = AsyncMock(return_value=1)
    redis.hgetall = AsyncMock(return_value={})
    redis.expire = AsyncMock(return_value=True)
    redis.ping = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1_000_000)  # 10.00000 credits remaining
    redis.exists = AsyncMock(return_value=0)
    redis.delete = AsyncMock(return_value=1)
    redis.keys = AsyncMock(return_value=[])
    return redis


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock AsyncSession — no real DB connection."""
    return AsyncMock()


@pytest.fixture
def sample_customer() -> MagicMock:
    """Sample CreditsCustomer with 10 EUR credit at default test rate."""
    customer = MagicMock(spec=CreditsCustomer)
    customer.id = 101
    customer.user_id = 55
    customer.credit_code = "12345678"
    customer.current_credits = Decimal("10.00000")
    customer.currency_code = "eur"
    customer.is_blocked = False
    customer.is_deleted = False
    return customer


@pytest.fixture
def sample_did() -> MagicMock:
    """Sample SiteIvrNumber — type 2 (direct dial), English, no country."""
    did = MagicMock(spec=SiteIvrNumber)
    did.id = 1
    did.number = "442071234567"
    did.type_id = 2
    did.language_id = 4
    did.country_id = None
    did.group_id = 3
    return did


@pytest_asyncio.fixture
async def async_client(mock_db: AsyncMock, mock_redis: AsyncMock) -> AsyncClient:
    """
    Async HTTP test client with DB and Redis overridden to mocks.

    Yields an httpx.AsyncClient pointed at the FastAPI app.
    No real DB or Redis required — unit tests only.
    """
    async def override_get_db():
        yield mock_db

    async def override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
