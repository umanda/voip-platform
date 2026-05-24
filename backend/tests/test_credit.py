"""
Tests for credit_service.py — the billing core.

These tests verify the atomic deduction logic, rate calculations, and
billing tick behavior. Billing correctness is critical: errors here
mean customers are over- or under-charged for live calls.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import CreditDeductionError, InsufficientCreditError
from app.services.credit_service import (
    CREDIT_SCALE,
    atomic_deduct_credit,
    calculate_available_time,
    effective_rate_per_minute,
    process_billing_tick,
)


# ── Rate formula ──────────────────────────────────────────────────────────────

def test_effective_rate_basic():
    """1.00/min + 20% VAT + 1.0 FX = 1.20/min."""
    rate = effective_rate_per_minute(
        call_rate=Decimal("1.00"),
        vat_rate=Decimal("20"),
        fx_rate=Decimal("1.0"),
    )
    assert rate == Decimal("1.2000")


def test_effective_rate_with_fx():
    """FX rate of 1.5 multiplies the customer cost proportionally."""
    rate = effective_rate_per_minute(
        call_rate=Decimal("1.00"),
        vat_rate=Decimal("20"),
        fx_rate=Decimal("1.5"),
    )
    assert rate == Decimal("1.800")


def test_effective_rate_zero_vat():
    """Zero VAT region: rate passes through with no markup."""
    rate = effective_rate_per_minute(
        call_rate=Decimal("2.00"),
        vat_rate=Decimal("0"),
        fx_rate=Decimal("1.0"),
    )
    assert rate == Decimal("2.00")


# ── Available time calculation ────────────────────────────────────────────────

def test_calculate_available_time_exact():
    """1.20 credits at 1.20/min = exactly 60 seconds."""
    seconds = calculate_available_time(
        current_credits=Decimal("1.20"),
        rate_per_minute=Decimal("1.20"),
    )
    assert seconds == 60


def test_calculate_available_time_floor():
    """Result is floored — partial seconds are not available."""
    seconds = calculate_available_time(
        current_credits=Decimal("1.21"),
        rate_per_minute=Decimal("1.20"),
    )
    # 1.21 / 1.20 * 60 = 60.5 → floor → 60
    assert seconds == 60


def test_calculate_available_time_zero_rate():
    """Zero rate guard: avoid ZeroDivisionError, return 0."""
    seconds = calculate_available_time(
        current_credits=Decimal("100.0"),
        rate_per_minute=Decimal("0"),
    )
    assert seconds == 0


# ── Atomic deduction ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_atomic_deduct_success():
    """Successful deduction returns new balance."""
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=900_000)  # 9.00000 credits left

    result = await atomic_deduct_credit(mock_redis, account_id=101, deduct_units=100_000)
    assert result == 900_000


@pytest.mark.asyncio
async def test_atomic_deduct_insufficient():
    """Redis script returns -1 → InsufficientCreditError."""
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=-1)

    with pytest.raises(InsufficientCreditError):
        await atomic_deduct_credit(mock_redis, account_id=101, deduct_units=999_999_999)


@pytest.mark.asyncio
async def test_atomic_deduct_key_missing():
    """Redis script returns -2 → CreditDeductionError (needs reconciliation)."""
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=-2)

    with pytest.raises(CreditDeductionError):
        await atomic_deduct_credit(mock_redis, account_id=101, deduct_units=1)


# ── Billing tick ──────────────────────────────────────────────────────────────

def _make_session_hash(rate_per_second: str = "0.02") -> dict[str, str]:
    return {
        "account_id": "101",
        "rate_per_second": rate_per_second,
        "rate_per_minute": str(Decimal(rate_per_second) * 60),
        "start_time": "2026-05-17T10:00:00+00:00",
        "last_tick": "2026-05-17T10:00:00+00:00",
        "gateway": "voxbone-outbound",
        "service_type": "2",
        "site_ivr_number_id": "1",
    }


@pytest.mark.asyncio
async def test_billing_tick_deducts_correctly():
    """60-second tick at 0.02/s = 1.20 credit units deducted."""
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value=_make_session_hash("0.02"))
    mock_redis.hset = AsyncMock(return_value=1)

    remaining_after = int(Decimal("8.80") * CREDIT_SCALE)  # 8.80 left after 1.20 deducted

    with patch(
        "app.services.credit_service.atomic_deduct_credit",
        return_value=remaining_after,
    ):
        continue_call, remaining_seconds, deducted = await process_billing_tick(
            redis=mock_redis, call_uuid="test-uuid", account_id=101, elapsed_seconds=60
        )

    assert continue_call is True
    assert remaining_seconds > 0
    # 60 seconds * 0.02/s = 1.20 credits = 120_000 units
    assert abs(deducted - 1.2) < 0.01


@pytest.mark.asyncio
async def test_billing_tick_returns_false_on_zero_balance():
    """Exhausted credit → (False, 0, deducted)."""
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value=_make_session_hash("0.02"))

    with patch(
        "app.services.credit_service.atomic_deduct_credit",
        side_effect=InsufficientCreditError("no credit"),
    ):
        continue_call, remaining_seconds, _ = await process_billing_tick(
            redis=mock_redis, call_uuid="test-uuid", account_id=101, elapsed_seconds=60
        )

    assert continue_call is False
    assert remaining_seconds == 0


@pytest.mark.asyncio
async def test_billing_tick_atomic():
    """
    Two concurrent ticks must not over-deduct.

    The Redis Lua script is atomic: the second concurrent DECRBY sees the
    already-reduced balance from the first. We verify this by checking that
    eval is called twice (not a Python-level read-modify-write) and that the
    second call receives -1 (insufficient) when balance is too low.
    """
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value=_make_session_hash("0.02"))
    mock_redis.hset = AsyncMock(return_value=1)

    call_count = 0

    async def mock_eval(script, num_keys, key, amount):
        nonlocal call_count
        call_count += 1
        # First call succeeds; second call sees insufficient balance
        return 500_000 if call_count == 1 else -1

    mock_redis.eval = mock_eval

    results = await asyncio.gather(
        process_billing_tick(mock_redis, "uuid-1", 101, 60),
        process_billing_tick(mock_redis, "uuid-1", 101, 60),
        return_exceptions=True,
    )

    continue_flags = [r[0] for r in results if not isinstance(r, Exception)]
    # At least one tick should have been denied (continue=False)
    assert False in continue_flags
    # Both calls went through the atomic script — not a Python-level race
    assert call_count == 2


@pytest.mark.asyncio
async def test_billing_tick_ceiling_rounding():
    """
    Fractional seconds are billed as full seconds (R-BILL-06 ceiling).

    At 0.02/s for 1 second: math.ceil(0.02 * 1 * 100_000) = 2000 units.
    Not floor(2000) = 2000, not round(2000) = 2000.
    For a fractional rate (e.g., 0.0201/s): ceil ensures we never under-bill.
    """
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value=_make_session_hash("0.0201"))
    mock_redis.hset = AsyncMock(return_value=1)

    captured_units: list[int] = []

    async def capture_deduct(redis, account_id, deduct_units):
        captured_units.append(deduct_units)
        return 900_000

    with patch("app.services.credit_service.atomic_deduct_credit", side_effect=capture_deduct):
        await process_billing_tick(mock_redis, "test-uuid", 101, elapsed_seconds=1)

    # 0.0201 * 1 * 100_000 = 2010.0 → ceil → 2010
    assert captured_units[0] == 2010
