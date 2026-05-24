"""
billing_worker/services/cdr_service.py — CDR persistence layer.

Handles all PostgreSQL writes for the billing worker:
  - finalize_statistics: UPDATE the statistics stub row created at authorize time
  - append_tracing:      INSERT one append-only tracing event row (R-BILL-02)
  - update_consultant_status: SET consultants.ivr_status (1=online, 2=busy)

Telecom rules:
  R-BILL-02: Tracings are append-only. Never UPDATE or DELETE a tracing row.
  R-BILL-03: CDR must be persisted to DB on every hangup; write even if credit
             deduction fails (log the discrepancy, do not skip the write).

Migration notes:
  Legacy: Sentinel/PHP wrote CDRs in synchronous HTTP call during ESL hangup.
  New:    Async SQLAlchemy write from billing worker (no FS dialplan blocked).
"""

import math
from datetime import datetime, UTC
from decimal import Decimal

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.consultants import Consultant
from app.models.db.statistics import Statistics
from app.models.db.tracings import Tracing, TracingStatus

log = structlog.get_logger(__name__)

# Sentinel status strings (legacy column values — DO NOT rename)
_HANGUP_STATUS_NORMAL = "NORMAL"
_HANGUP_STATUS_SHORT = "SHORT CALL"
_HANGUP_STATUS_NO_ANSWER = "NO ANSWER"
_HANGUP_STATUS_BUSY = "REMOTE BUSY"
_HANGUP_STATUS_CANCEL = "CUSTOMER_HANGUP_BEFORE_ANSWER"
_HANGUP_STATUS_DISCONNECT = "DISCONNECT"


def map_hangup_status(hangup_cause: str, billsec: int, short_call_threshold: int) -> str:
    """
    Map a FreeSWITCH hangup cause + billsec to the legacy statistics.status string.

    These string values must match the legacy Sentinel system exactly —
    downstream billing reports filter on them.
    """
    if billsec >= short_call_threshold:
        return _HANGUP_STATUS_NORMAL
    if billsec > 0:
        return _HANGUP_STATUS_SHORT
    if hangup_cause in ("NO_ANSWER", "NO_USER_RESPONSE"):
        return _HANGUP_STATUS_NO_ANSWER
    if hangup_cause == "USER_BUSY":
        return _HANGUP_STATUS_BUSY
    if hangup_cause in ("ORIGINATOR_CANCEL", "NORMAL_CLEARING"):
        return _HANGUP_STATUS_CANCEL
    return _HANGUP_STATUS_DISCONNECT


async def finalize_statistics(
    db: AsyncSession,
    statistics_id: int,
    hangup_time: datetime,
    end_time: datetime,
    connected_time: datetime | None,
    total_duration: int,
    conversation_duration: int,
    hangup_cause: str,
    billsec: int,
    credit_after: Decimal,
    rate_per_second: Decimal,
    short_call_threshold: int,
) -> None:
    """
    UPDATE the statistics stub row with final call data.

    The stub was created (with start_time, rate, customer IDs) during
    /v1/call/authorize. This function fills in all timing and billing fields
    once the call ends.

    Args:
        statistics_id:       PK of the statistics row to update.
        hangup_time:         When the B-leg (consultant) hung up.
        end_time:            When the A-leg (caller) released.
        connected_time:      When the call was answered (None if unanswered).
        total_duration:      Seconds from SIP INVITE to final disconnect.
        conversation_duration: Billed seconds (only when both legs were connected).
        hangup_cause:        FreeSWITCH Hangup-Cause string.
        billsec:             Canonical billed seconds from FreeSWITCH event.
        credit_after:        Customer credit balance after the call cost.
        rate_per_second:     Per-second rate (for consultant_total_earning).
        short_call_threshold: Seconds below which a call is "SHORT CALL".

    Telecom note:
        Statistics rows are NOT append-only — legacy Sentinel updated them
        throughout the call lifecycle. Our worker updates once at hangup.
    """
    status = map_hangup_status(hangup_cause, billsec, short_call_threshold)
    consultant_total_earning = Decimal(str(math.ceil(billsec * float(rate_per_second) * 100))) / 100

    await db.execute(
        update(Statistics)
        .where(Statistics.id == statistics_id)
        .values(
            connected_time=connected_time.replace(tzinfo=None) if connected_time else None,
            hangup_time=hangup_time.replace(tzinfo=None),
            end_time=end_time.replace(tzinfo=None),
            total_duration=total_duration,
            conversation_duration=conversation_duration,
            status=status,
            credit_after=credit_after,
            consultant_total_earning=consultant_total_earning,
        )
    )
    await db.commit()

    log.info(
        "statistics_finalized",
        statistics_id=statistics_id,
        status=status,
        billsec=billsec,
        conversation_duration=conversation_duration,
    )


async def append_tracing(
    db: AsyncSession,
    statistics_id: int,
    status: TracingStatus,
    timestamp: datetime,
    info: str | None = None,
    credit_before: Decimal | None = None,
    credit_after: Decimal | None = None,
) -> None:
    """
    INSERT one tracing row. Never call UPDATE on tracings (R-BILL-02).

    Args:
        statistics_id: FK → statistics.id
        status:        TracingStatus event code (e.g. CONNECTED_TIME = 4)
        timestamp:     When this event occurred (UTC).
        info:          Optional free-text note (hangup cause, error msg, etc.)
        credit_before: Credit snapshot before this event (optional).
        credit_after:  Credit snapshot after this event (optional).
    """
    row = Tracing(
        statistics_id=statistics_id,
        timestamp=timestamp.replace(tzinfo=None),
        status=int(status),
        info=info,
        credit_before=credit_before,
        credit_after=credit_after,
    )
    db.add(row)
    await db.commit()

    log.debug("tracing_appended", statistics_id=statistics_id, status=status.name)


async def set_connected_time(
    db: AsyncSession,
    statistics_id: int,
    connected_time: datetime,
) -> None:
    """
    UPDATE statistics.connected_time when the call is answered (CHANNEL_ANSWER).

    This is the only mid-call UPDATE to statistics — all others happen at hangup.
    """
    await db.execute(
        update(Statistics)
        .where(Statistics.id == statistics_id)
        .values(connected_time=connected_time.replace(tzinfo=None))
    )
    await db.commit()


async def update_consultant_status(
    db: AsyncSession,
    consultant_id: int,
    ivr_status: int,
) -> None:
    """
    SET consultants.ivr_status for a consultant.

    ivr_status values: 1=online (available), 2=busy (in call), 3=offline.
    Called on CHANNEL_ANSWER (set 2) and CHANNEL_HANGUP_COMPLETE (set 1).

    Telecom risk (HIGH-04): If the billing worker crashes between CHANNEL_ANSWER
    and hangup, the consultant stays stuck at ivr_status=2 (busy). The reconcile
    handler detects this on startup and resets status to 1.
    """
    await db.execute(
        update(Consultant)
        .where(Consultant.id == consultant_id)
        .values(ivr_status=ivr_status)
    )
    await db.commit()

    log.info(
        "consultant_status_updated",
        consultant_id=consultant_id,
        ivr_status=ivr_status,
    )
