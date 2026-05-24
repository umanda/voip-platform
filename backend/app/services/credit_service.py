import math
from datetime import datetime, UTC
from decimal import Decimal

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CreditDeductionError,
    InsufficientCreditError,
    RedisSessionNotFoundError,
)
from app.models.db.credits_customers import CreditsCustomer

# ── Redis key patterns ────────────────────────────────────────────────────────
CREDIT_KEY = "credit:{account_id}"              # integer units (CREDIT_SCALE)
CALL_SESSION_KEY = "call:{call_uuid}"           # hash — per-call billing state
CONCURRENT_CALLS_KEY = "concurrent:{account_id}"  # integer counter
ACCOUNT_CACHE_KEY = "account:{token}"           # JSON string, TTL 5 min

# Convert decimal(10,5) credits to integer units for atomic Redis operations.
# 1 credit = 100 000 units → preserves all 5 decimal places of DB precision.
CREDIT_SCALE = 100_000

# ── Atomic Redis deduction script (R-BILL-01) ─────────────────────────────────
# Never use GET + application-level check + SET — that is a race condition.
# This Lua script executes atomically inside Redis.
# Returns:
#   new_balance  (≥ 0)  on success
#   -1           if balance < requested amount (insufficient credit)
#   -2           if key does not exist (session lost; needs reconciliation)
_DEDUCT_SCRIPT = """
local balance = redis.call('GET', KEYS[1])
if not balance then
    return -2
end
local bal = tonumber(balance)
local amount = tonumber(ARGV[1])
if bal < amount then
    return -1
end
return redis.call('DECRBY', KEYS[1], ARGV[1])
"""


def effective_rate_per_minute(
    call_rate: Decimal,
    vat_rate: Decimal,
    fx_rate: Decimal,
) -> Decimal:
    """
    Calculate the per-minute cost in customer credits.

    Replicates Sentinel BaseRepository::callRateWithVat():
      effective = call_rate * (1 + vat_rate / 100) * fx_rate

    Args:
        call_rate: Consultant's per-minute rate in their own currency.
        vat_rate: VAT percentage (e.g., Decimal("20") for 20%).
        fx_rate: Conversion from coach currency to customer credit currency.

    Telecom note:
        The fx_rate converts so that the result is in the customer's credit units.
        If both are in EUR, fx_rate = 1.0.
    """
    return call_rate * (1 + vat_rate / 100) * fx_rate


def calculate_available_time(
    current_credits: Decimal,
    rate_per_minute: Decimal,
) -> int:
    """
    Compute available call time in whole seconds (floor).

    Replicates Sentinel CustomerActionsRepository::availableCallBlock() logic:
      avb_time = current_credits / rate_per_second
               = (current_credits / rate_per_minute) * 60

    Args:
        current_credits: Customer's credit balance (decimal).
        rate_per_minute: Effective per-minute cost in customer credits.

    Returns:
        Integer seconds available. Returns 0 if rate is zero (guard).
    """
    if rate_per_minute <= 0:
        return 0
    return int((current_credits / rate_per_minute) * 60)


async def load_credit_to_redis(
    redis: aioredis.Redis,
    account_id: int,
    current_credits: Decimal,
) -> int:
    """
    Seed Redis with the customer's DB credit balance.

    Called on first lookup (cache miss). Uses SET NX to avoid overwriting
    a balance that is already being tracked for an in-progress call.

    Returns:
        Integer units stored (or currently in Redis if key already existed).
    """
    units = int(current_credits * CREDIT_SCALE)
    key = CREDIT_KEY.format(account_id=account_id)
    await redis.set(key, units, nx=True)
    return units


async def get_credit_balance_units(
    redis: aioredis.Redis,
    account_id: int,
    db: AsyncSession,
    customer: CreditsCustomer | None = None,
) -> int:
    """
    Return the customer's credit balance as integer Redis units.

    Checks Redis first (sub-ms). Falls back to PostgreSQL on cache miss,
    then seeds Redis for subsequent calls (R-BILL-01 pattern).

    Args:
        account_id: credits_customers.id
        customer: ORM object if already loaded (avoids a second DB round-trip).
    """
    key = CREDIT_KEY.format(account_id=account_id)
    val = await redis.get(key)
    if val is not None:
        return int(val)

    # Cache miss — load from DB
    if customer is None:
        result = await db.execute(
            select(CreditsCustomer).where(CreditsCustomer.id == account_id)
        )
        customer = result.scalar_one_or_none()

    if customer is None:
        return 0

    return await load_credit_to_redis(
        redis, account_id, Decimal(str(customer.current_credits))
    )


async def atomic_deduct_credit(
    redis: aioredis.Redis,
    account_id: int,
    deduct_units: int,
) -> int:
    """
    Atomically deduct credit using a Redis Lua script (R-BILL-01).

    This is the ONLY correct way to deduct credit. It runs inside Redis
    as a single atomic operation — no GET/SET race condition possible.

    Args:
        account_id: credits_customers.id
        deduct_units: Amount in CREDIT_SCALE units (not raw credits).

    Returns:
        New balance in Redis units (≥ 0).

    Raises:
        InsufficientCreditError: Balance < deduct_units.
        CreditDeductionError: Key not found — billing worker must reconcile.
    """
    key = CREDIT_KEY.format(account_id=account_id)
    result = await redis.eval(_DEDUCT_SCRIPT, 1, key, deduct_units)
    result = int(result)

    if result == -2:
        raise CreditDeductionError(
            f"credit:{account_id} key missing in Redis — "
            "possible failover; billing worker will reconcile (R-BILL-05)"
        )
    if result == -1:
        raise InsufficientCreditError(
            f"Account {account_id} insufficient credit: "
            f"requested {deduct_units} units"
        )
    return result


async def create_call_session(
    redis: aioredis.Redis,
    call_uuid: str,
    account_id: int,
    rate_per_minute: Decimal,
    gateway: str,
    service_type: int,
    site_ivr_number_id: int,
    consultant_id: int | None = None,
    statistics_id: int | None = None,
    credit_before: Decimal = Decimal("0"),
    initial_deducted_units: int = 0,
) -> None:
    """
    Create the per-call Redis session at authorize time.

    This hash is the billing worker's source of truth during the call.
    TTL is 7200s (2 hours) — longer than any expected max call duration.

    Key: call:{call_uuid} → hash {account_id, rate_per_second, ...}

    Phase 4 fields:
        statistics_id: FK to statistics table row created at authorize time.
        consultant_id: FK to consultants table — used to reset ivr_status on hangup.
        credit_before: Customer credit balance before this call (for CDR snapshot).
        total_deducted_units: Running sum of all deductions (block + ticks) in CREDIT_SCALE
                              units. Used at hangup to compute refund for short calls.

    Telecom note:
        The billing worker reads this on CHANNEL_HANGUP_COMPLETE to finalize
        the CDR. If this key is missing at hangup, the worker uses R-BILL-05
        reconciliation to compute what it can from FS ESL event variables.
    """
    key = CALL_SESSION_KEY.format(call_uuid=call_uuid)
    rate_per_second = rate_per_minute / 60
    now = datetime.now(UTC).isoformat()

    await redis.hset(
        key,
        mapping={
            "account_id": str(account_id),
            "rate_per_second": str(rate_per_second),
            "rate_per_minute": str(rate_per_minute),
            "start_time": now,
            "last_tick": now,
            "gateway": gateway,
            "service_type": str(service_type),
            "site_ivr_number_id": str(site_ivr_number_id),
            "consultant_id": str(consultant_id) if consultant_id is not None else "",
            "statistics_id": str(statistics_id) if statistics_id is not None else "",
            "credit_before": str(credit_before),
            "total_deducted_units": str(initial_deducted_units),
        },
    )
    await redis.expire(key, 7200)


async def get_call_session(
    redis: aioredis.Redis,
    call_uuid: str,
) -> dict[str, str]:
    """
    Retrieve call session hash from Redis.

    Raises:
        RedisSessionNotFoundError: Key not in Redis (expired or never created).
    """
    key = CALL_SESSION_KEY.format(call_uuid=call_uuid)
    session = await redis.hgetall(key)
    if not session:
        raise RedisSessionNotFoundError(
            f"No active call session for UUID {call_uuid} — "
            "key may have expired or call was never authorized"
        )
    return session


async def process_billing_tick(
    redis: aioredis.Redis,
    call_uuid: str,
    account_id: int,
    elapsed_seconds: int,
) -> tuple[bool, int, float]:
    """
    Process one billing tick: deduct credit for elapsed_seconds.

    Called by Lua every 60 seconds during an active call.
    Replaces Sentinel POST /api/v1/check/call/time (block-based) with
    continuous per-tick deduction — simpler and less prone to timing races.

    Ceiling rounding (R-BILL-06): partial seconds billed as full seconds.

    Args:
        call_uuid: FreeSWITCH UUID (Redis session key).
        account_id: credits_customers.id.
        elapsed_seconds: Seconds to bill (typically 60).

    Returns:
        (continue_call, remaining_seconds, deducted_amount_credits)
        If continue_call is False, Lua must hangup immediately (R-FLOW-02).

    Telecom note:
        On any failure (InsufficientCreditError, CreditDeductionError),
        we return continue_call=False. It is always safer to end the call
        than to allow unbilled call time.
    """
    session = await get_call_session(redis, call_uuid)
    rate_per_second = Decimal(session["rate_per_second"])

    # R-BILL-06: ceiling — caller pays for partial seconds as full
    deduct_units = math.ceil(float(rate_per_second) * elapsed_seconds * CREDIT_SCALE)

    try:
        new_balance_units = await atomic_deduct_credit(redis, account_id, deduct_units)
    except (InsufficientCreditError, CreditDeductionError):
        return False, 0, float(deduct_units / CREDIT_SCALE)

    # Update last_tick and accumulate total deducted units for hangup reconciliation
    tick_key = CALL_SESSION_KEY.format(call_uuid=call_uuid)
    await redis.hset(tick_key, "last_tick", datetime.now(UTC).isoformat())
    await redis.hincrby(tick_key, "total_deducted_units", deduct_units)

    remaining_credits = Decimal(new_balance_units) / CREDIT_SCALE
    remaining_seconds = calculate_available_time(remaining_credits, rate_per_second * 60)

    from app.config import get_settings
    min_seconds = get_settings().credit_min_seconds
    continue_call = remaining_seconds >= min_seconds

    return continue_call, remaining_seconds, float(deduct_units / CREDIT_SCALE)
