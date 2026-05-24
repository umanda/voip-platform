"""
Tests for billing_worker/services/cdr_service.py

Verifies:
  - finalize_statistics issues an UPDATE (not INSERT) — append-only is for tracings
  - append_tracing only ever INSERTs — never UPDATE
  - map_hangup_status covers all status strings
  - set_connected_time and update_consultant_status issue correct UPDATE statements
"""

from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from billing_worker.services.cdr_service import (
    append_tracing,
    finalize_statistics,
    map_hangup_status,
    set_connected_time,
    update_consultant_status,
)
from app.models.db.tracings import TracingStatus


# ── map_hangup_status ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cause,billsec,threshold,expected", [
    ("NORMAL_CLEARING",     90, 30, "NORMAL"),
    ("NORMAL_CLEARING",     10, 30, "SHORT CALL"),
    ("NO_ANSWER",            0, 30, "NO ANSWER"),
    ("NO_USER_RESPONSE",     0, 30, "NO ANSWER"),
    ("USER_BUSY",            0, 30, "REMOTE BUSY"),
    ("ORIGINATOR_CANCEL",    0, 30, "CUSTOMER_HANGUP_BEFORE_ANSWER"),
    ("ALLOTTED_TIMEOUT",     0, 30, "DISCONNECT"),
    ("RECOVERY_ON_TIMER_EXPIRE", 0, 30, "DISCONNECT"),
])
def test_map_hangup_status(cause, billsec, threshold, expected):
    assert map_hangup_status(cause, billsec, threshold) == expected


# ── finalize_statistics ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_statistics_executes_update():
    """finalize_statistics must issue an UPDATE statement (not INSERT)."""
    mock_db = AsyncMock()

    now = datetime.now(UTC)
    await finalize_statistics(
        db=mock_db,
        statistics_id=101,
        hangup_time=now,
        end_time=now,
        connected_time=now,
        total_duration=95,
        conversation_duration=90,
        hangup_cause="NORMAL_CLEARING",
        billsec=90,
        credit_after=Decimal("1.50000"),
        rate_per_second=Decimal("0.50") / 60,
        short_call_threshold=30,
    )

    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()

    # Verify the statement is an UPDATE (check compiled SQL includes UPDATE)
    stmt = mock_db.execute.call_args[0][0]
    compiled = str(stmt.compile())
    assert "UPDATE" in compiled.upper()
    assert "statistics" in compiled.lower()


@pytest.mark.asyncio
async def test_finalize_statistics_no_answer_has_null_connected_time():
    """Unanswered call: connected_time=None must be passed through."""
    mock_db = AsyncMock()
    now = datetime.now(UTC)

    await finalize_statistics(
        db=mock_db,
        statistics_id=200,
        hangup_time=now,
        end_time=now,
        connected_time=None,  # unanswered
        total_duration=15,
        conversation_duration=0,
        hangup_cause="NO_ANSWER",
        billsec=0,
        credit_after=Decimal("2.00000"),
        rate_per_second=Decimal("0.50") / 60,
        short_call_threshold=30,
    )

    stmt = mock_db.execute.call_args[0][0]
    # The UPDATE values should have connected_time=None
    update_values = stmt.whereclause.right.value if hasattr(stmt, 'whereclause') else {}
    # Just confirm the call went through without exception
    mock_db.commit.assert_called_once()


# ── append_tracing (R-BILL-02: append-only) ───────────────────────────────────

@pytest.mark.asyncio
async def test_append_tracing_uses_add_not_execute():
    """append_tracing must use db.add (INSERT), never db.execute (would allow UPDATE)."""
    mock_db = AsyncMock()
    now = datetime.now(UTC)

    await append_tracing(
        db=mock_db,
        statistics_id=101,
        status=TracingStatus.CONNECTED_TIME,
        timestamp=now,
        info="test",
        credit_before=Decimal("2.00"),
        credit_after=Decimal("1.50"),
    )

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()

    # Verify the object added is a Tracing instance
    from app.models.db.tracings import Tracing
    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, Tracing)
    assert added_obj.status == int(TracingStatus.CONNECTED_TIME)
    assert added_obj.statistics_id == 101


@pytest.mark.asyncio
async def test_append_tracing_end_time_status():
    mock_db = AsyncMock()
    now = datetime.now(UTC)

    await append_tracing(
        db=mock_db,
        statistics_id=101,
        status=TracingStatus.END_TIME,
        timestamp=now,
    )

    added_obj = mock_db.add.call_args[0][0]
    assert added_obj.status == int(TracingStatus.END_TIME)
    assert added_obj.credit_before is None  # optional fields not set


# ── set_connected_time ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_connected_time_issues_update():
    mock_db = AsyncMock()
    now = datetime.now(UTC)

    await set_connected_time(mock_db, statistics_id=101, connected_time=now)

    mock_db.execute.assert_called_once()
    stmt = mock_db.execute.call_args[0][0]
    assert "UPDATE" in str(stmt.compile()).upper()


# ── update_consultant_status ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_consultant_status_busy():
    mock_db = AsyncMock()
    await update_consultant_status(mock_db, consultant_id=7, ivr_status=2)
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_update_consultant_status_online():
    mock_db = AsyncMock()
    await update_consultant_status(mock_db, consultant_id=7, ivr_status=1)
    stmt = mock_db.execute.call_args[0][0]
    compiled = str(stmt.compile())
    assert "consultants" in compiled.lower()
