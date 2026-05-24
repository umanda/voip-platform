import json
from decimal import Decimal

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AccountNotFoundError, AccountSuspendedError
from app.models.db.countries import Country
from app.models.db.credits_customers import CreditsCustomer
from app.models.db.currency_exchange_rates import CurrencyExchangeRate
from app.models.db.users import User
from app.services.credit_service import ACCOUNT_CACHE_KEY, load_credit_to_redis

logger = structlog.get_logger(__name__)

ACCOUNT_CACHE_TTL = 300  # 5 minutes — balances cache duration vs stale block risk


async def get_customer_by_credit_code(
    db: AsyncSession,
    redis: aioredis.Redis,
    credit_code: str,
) -> CreditsCustomer:
    """
    Look up a credit customer by their 8-digit PIN (credit_code).

    The credit_code is the customer's primary IVR identifier — entered via
    DTMF or supplied from ivr_known_users pre-auth.

    Checks Redis account cache first (TTL: 5 min). Cache stores lightweight
    metadata for the blocked/deleted check. Full ORM object always comes from DB
    so that relationships (user, country) are available to callers.

    Args:
        credit_code: 8-digit numeric PIN.

    Returns:
        CreditsCustomer ORM instance (user relationship available).

    Raises:
        AccountNotFoundError: credit_code not found, or account/user deleted.
        AccountSuspendedError: is_blocked = true.
    """
    cache_key = ACCOUNT_CACHE_KEY.format(token=credit_code)
    cached = await redis.get(cache_key)

    account_id: int | None = None
    if cached:
        meta = json.loads(cached)
        if meta.get("is_blocked"):
            raise AccountSuspendedError(f"Account {credit_code} is blocked")
        if meta.get("is_deleted"):
            raise AccountNotFoundError(f"Account {credit_code} is deleted")
        account_id = meta.get("id")

    # Fetch full ORM object (needed for rate/FX calculations downstream)
    if account_id is not None:
        result = await db.execute(
            select(CreditsCustomer).where(CreditsCustomer.id == account_id)
        )
    else:
        result = await db.execute(
            select(CreditsCustomer)
            .where(CreditsCustomer.credit_code == credit_code)
            .where(CreditsCustomer.is_deleted.is_(False))
        )

    customer = result.scalar_one_or_none()

    if customer is None:
        raise AccountNotFoundError(f"No account found for credit_code ending ...{credit_code[-2:]}")

    # Validate user record
    user_result = await db.execute(select(User).where(User.id == customer.user_id))
    user = user_result.scalar_one_or_none()
    if user and user.is_deleted:
        raise AccountNotFoundError(f"Parent user deleted for account {customer.id}")

    if customer.is_blocked:
        raise AccountSuspendedError(f"Account {customer.id} is blocked")

    # Refresh cache
    await redis.set(
        cache_key,
        json.dumps({
            "id": customer.id,
            "credit_code": customer.credit_code,
            "currency_code": customer.currency_code,
            "is_blocked": customer.is_blocked,
            "is_deleted": customer.is_deleted,
        }),
        ex=ACCOUNT_CACHE_TTL,
    )

    # Seed Redis credit balance if key is absent (first call or after failover)
    await load_credit_to_redis(
        redis, customer.id, Decimal(str(customer.current_credits))
    )

    return customer


async def get_country_vat(
    db: AsyncSession,
    country_id: int,
) -> Decimal:
    """
    Return the effective VAT rate for a country as a percentage.

    Falls back to the Sentinel default (20%) if country not found.
    This matches Sentinel Configs/config.php default_vat_rate.
    """
    result = await db.execute(select(Country).where(Country.id == country_id))
    country = result.scalar_one_or_none()
    if country is None:
        return Decimal("20")
    return Decimal(str(country.effective_vat_rate))


async def get_fx_rate(
    db: AsyncSession,
    from_currency: str,
    to_currency: str,
) -> Decimal:
    """
    Get the exchange rate to convert from_currency → to_currency.

    Uses the most recent row in currency_exchange_rates (latest()->first()
    pattern from Sentinel BaseRepository).

    The rates JSON structure is not fully confirmed — schema-map.md SQL query 2
    must be run against the live DB to validate. Current implementation handles
    both {code: float} and {code: {rate: float}} formats.

    Returns 1.0 if same currency or if rates are unavailable (fail-safe).

    Telecom note:
        FX rates are updated by the main Helios scheduler, not Sentinel.
        Stale rates are a billing risk — monitor the currency_exchange_rates
        updated_at timestamp in CloudWatch.
    """
    if from_currency.lower() == to_currency.lower():
        return Decimal("1.0")

    result = await db.execute(
        select(CurrencyExchangeRate)
        .order_by(CurrencyExchangeRate.id.desc())
        .limit(1)
    )
    fx_row = result.scalar_one_or_none()

    if fx_row is None:
        logger.warning(
            "fx_rates_table_empty",
            from_currency=from_currency,
            to_currency=to_currency,
        )
        return Decimal("1.0")

    rates: dict = fx_row.rates
    rate_val = rates.get(from_currency.upper()) or rates.get(from_currency.lower())

    if rate_val is None:
        logger.warning(
            "fx_rate_not_found",
            from_currency=from_currency,
            to_currency=to_currency,
        )
        return Decimal("1.0")

    if isinstance(rate_val, dict):
        rate_val = rate_val.get("rate", 1.0)

    return Decimal(str(rate_val))
