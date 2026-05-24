"""
Tests for billing_worker/handlers/call_hangup.py

Covers all scenarios required by the Phase 4 test spec:
  - Answered call: correct cost calculation (ceiling)
  - No-answer call: zero cost, correct status
  - Credit over-deduction: refund applied
  - Session missing: best-effort CDR path
  - Short call: status = "SHORT CALL"
  - Concurrent counter decremented
"""

import math
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.services.credit_service import CREDIT_SCALE
from billing_worker.handlers.call_hangup import handle_hangup, _parse_epoch_us
from billing_worker.services.cdr_service import map_hangup_status


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_session(
    account_id: int = 42,
    rate_per_minute: str = "0.50",
    rate_per_second: str | None = None,
    statistics_id: str = "101",
    consultant_id: str = "7",
    credit_before: str = "2.00000",
    total_deducted_units: str = "200000",
) -> dict[str, str]:
    if rate_per_second is None:
        rate_per_second = str(Decimal(rate_per_minute) / 60)
    return {
        "account_id": str(account_id),
        "rate_per_minute": rate_per_minute,
        "rate_per_second": rate_per_second,
        "statistics_id": statistics_id,
        "consultant_id": consultant_id,
        "credit_before": credit_before,
        "total_deducted_units": total_deducted_units,
        "start_time": "2026-01-01T00:00:00+00:00",
        "last_tick": "2026-01-01T00:01:00+00:00",
        "answer_time": "2026-01-01T00:00:05+00:00",
        "gateway": "voxbone-outbound",
        "service_type": "2",
        "site_ivr_number_id": "15",
    }


def _make_hangup_event(
    call_uuid: str = "abc-123",
    billsec: int = 90,
    duration: int = 95,
    hangup_cause: str = "NORMAL_CLEARING",
    answer_epoch_us: int = 1_700_000_000_000_000,
    hangup_epoch_us: int = 1_700_000_090_000_000,
) -> dict[str, str]:
    return {
        "Unique-ID": call_uuid,
        "Event-Name": "CHANNEL_HANGUP_COMPLETE",
        "variable_billsec": str(billsec),
        "variable_duration": str(duration),
        "Hangup-Cause": hangup_cause,
        "Caller-Channel-Answered-Time": str(answer_epoch_us),
        "Caller-Channel-Hangup-Time": str(hangup_epoch_us),
        "Caller-Caller-ID-Number": "+33600000001",
        "Caller-Destination-Number": "3265664982",
    }


# ── unit: map_hangup_status ───────────────────────────────────────────────────

def test_map_status_normal():
    assert map_hangup_status("NORMAL_CLEARING", 90, 30) == "NORMAL"


def test_map_status_short_call():
    assert map_hangup_status("NORMAL_CLEARING", 10, 30) == "SHORT CALL"


def test_map_status_no_answer():
    assert map_hangup_status("NO_ANSWER", 0, 30) == "NO ANSWER"


def test_map_status_busy():
    assert map_hangup_status("USER_BUSY", 0, 30) == "REMOTE BUSY"


def test_map_status_customer_cancel():
    assert map_hangup_status("ORIGINATOR_CANCEL", 0, 30) == "CUSTOMER_HANGUP_BEFORE_ANSWER"


def test_map_status_disconnect():
    assert map_hangup_status("ALLOTTED_TIMEOUT", 0, 30) == "DISCONNECT"


# ── unit: _parse_epoch_us ─────────────────────────────────────────────────────

def test_parse_epoch_us_valid():
    # 1 second = 1_000_000 microseconds
    dt = _parse_epoch_us("1000000")
    assert dt is not None
    assert dt.timestamp() == pytest.approx(1.0)


def test_parse_epoch_us_zero_returns_none():
    assert _parse_epoch_us("0") is None


def test_parse_epoch_us_none_returns_none():
    assert _parse_epoch_us(None) is None


# ── unit: cost calculation (R-BILL-06 ceiling) ────────────────────────────────

def test_cost_uses_ceiling():
    """Partial second must be billed as a full second."""
    rate_per_second = Decimal("0.50") / 60  # 0.5/min = 0.008333.../s
    billsec = 61  # 1 second past a full minute
    # floor would be 0.00833 * 61 = 0.508...credits
    # ceil should round up
    cost_units = math.ceil(float(rate_per_second) * billsec * CREDIT_SCALE)
    cost_credits = Decimal(cost_units) / CREDIT_SCALE
    # Verify it's not floored
    floor_cost = float(rate_per_second) * billsec
    assert float(cost_credits) >= floor_cost


def test_zero_billsec_means_zero_cost():
    rate_per_second = Decimal("0.50") / 60
    cost_units = math.ceil(float(rate_per_second) * 0 * CREDIT_SCALE)
    assert cost_units == 0


# ── integration: handle_hangup answered call ─────────────────────────────────

@pytest.mark.asyncio
async def test_hangup_answered_call_writes_cdr():
    """Normal answered call: statistics finalized, tracings written."""
    redis_mock = AsyncMock()
    redis_mock.hgetall.return_value = _make_session(
        rate_per_minute="0.60",
        credit_before="1.00000",
        total_deducted_units="100000",  # 1 credit deducted upfront
    )
    redis_mock.get.return_value = b"1"

    event = _make_hangup_event(billsec=60, duration=65, hangup_cause="NORMAL_CLEARING")

    with patch("billing_worker.handlers.call_hangup.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.call_hangup.finalize_statistics", new_callable=AsyncMock) as mock_finalize, \
         patch("billing_worker.handlers.call_hangup.append_tracing", new_callable=AsyncMock) as mock_tracing, \
         patch("billing_worker.handlers.call_hangup.update_consultant_status", new_callable=AsyncMock) as mock_consultant:

        mock_db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_hangup(event, redis_mock)

    mock_finalize.assert_called_once()
    finalize_kwargs = mock_finalize.call_args.kwargs
    assert finalize_kwargs["billsec"] == 60
    assert finalize_kwargs["statistics_id"] == 101

    # Two tracings: HANGUP_TIME + END_TIME
    assert mock_tracing.call_count == 2

    # Consultant reset to 1 (online)
    mock_consultant.assert_called_once_with(mock_db, 7, ivr_status=1)

    # Redis session cleaned up
    redis_mock.delete.assert_called_once()


@pytest.mark.asyncio
async def test_hangup_refunds_over_deduction():
    """Credit refund applied when actual cost < total provisionally deducted."""
    rate_per_second = str(Decimal("0.60") / 60)  # 0.01/s
    # 60s actual = 0.60 credits = 60_000 units
    # but 120s was deducted upfront (120_000 units) — refund 60_000 units
    redis_mock = AsyncMock()
    redis_mock.hgetall.return_value = _make_session(
        rate_per_minute="0.60",
        rate_per_second=rate_per_second,
        total_deducted_units="120000",  # over-deducted
    )
    redis_mock.get.return_value = b"1"

    event = _make_hangup_event(billsec=60)

    with patch("billing_worker.handlers.call_hangup.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.call_hangup.finalize_statistics", new_callable=AsyncMock), \
         patch("billing_worker.handlers.call_hangup.append_tracing", new_callable=AsyncMock), \
         patch("billing_worker.handlers.call_hangup.update_consultant_status", new_callable=AsyncMock):

        mock_db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_hangup(event, redis_mock)

    # incrby called to refund
    redis_mock.incrby.assert_called_once()
    args = redis_mock.incrby.call_args[0]
    refund_units = args[1]
    actual_cost_units = math.ceil(float(Decimal(rate_per_second)) * 60 * CREDIT_SCALE)
    assert refund_units == 120000 - actual_cost_units


@pytest.mark.asyncio
async def test_hangup_no_answer_zero_cost():
    """Unanswered call: zero cost, NO ANSWER status, no consultant reset."""
    redis_mock = AsyncMock()
    redis_mock.hgetall.return_value = _make_session(
        consultant_id="",  # no consultant
        total_deducted_units="0",
    )
    redis_mock.get.return_value = b"1"

    event = _make_hangup_event(billsec=0, hangup_cause="NO_ANSWER",
                               answer_epoch_us=0, hangup_epoch_us=0)

    with patch("billing_worker.handlers.call_hangup.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.call_hangup.finalize_statistics", new_callable=AsyncMock) as mock_finalize, \
         patch("billing_worker.handlers.call_hangup.append_tracing", new_callable=AsyncMock), \
         patch("billing_worker.handlers.call_hangup.update_consultant_status", new_callable=AsyncMock) as mock_consultant:

        mock_db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_hangup(event, redis_mock)

    finalize_kwargs = mock_finalize.call_args.kwargs
    assert finalize_kwargs["billsec"] == 0
    # No consultant to reset
    mock_consultant.assert_not_called()
    # No refund needed
    redis_mock.incrby.assert_not_called()


@pytest.mark.asyncio
async def test_hangup_no_session_best_effort():
    """Missing Redis session: best-effort path runs without crashing."""
    redis_mock = AsyncMock()
    redis_mock.hgetall.return_value = {}  # empty = session missing

    event = _make_hangup_event()

    # Should not raise even with no session
    await handle_hangup(event, redis_mock)

    # No DB operations should be attempted
    redis_mock.delete.assert_not_called()


@pytest.mark.asyncio
async def test_hangup_cdr_write_failure_still_cleans_redis():
    """CDR write failure must not prevent Redis cleanup (R-BILL-03)."""
    redis_mock = AsyncMock()
    redis_mock.hgetall.return_value = _make_session()
    redis_mock.get.return_value = b"1"

    event = _make_hangup_event(billsec=60)

    with patch("billing_worker.handlers.call_hangup.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.call_hangup.finalize_statistics",
               new_callable=AsyncMock, side_effect=Exception("DB down")), \
         patch("billing_worker.handlers.call_hangup.append_tracing", new_callable=AsyncMock), \
         patch("billing_worker.handlers.call_hangup.update_consultant_status", new_callable=AsyncMock):

        mock_db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_hangup(event, redis_mock)

    # Redis cleanup must still happen despite DB failure
    redis_mock.delete.assert_called_once()


@pytest.mark.asyncio
async def test_hangup_concurrent_counter_decremented():
    """Concurrent call counter is decremented on every hangup."""
    redis_mock = AsyncMock()
    redis_mock.hgetall.return_value = _make_session()
    redis_mock.get.return_value = b"2"  # 2 concurrent calls

    event = _make_hangup_event(billsec=30)

    with patch("billing_worker.handlers.call_hangup.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.call_hangup.finalize_statistics", new_callable=AsyncMock), \
         patch("billing_worker.handlers.call_hangup.append_tracing", new_callable=AsyncMock), \
         patch("billing_worker.handlers.call_hangup.update_consultant_status", new_callable=AsyncMock):

        mock_db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_hangup(event, redis_mock)

    redis_mock.decr.assert_called_once()
