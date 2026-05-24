"""
billing_worker/handlers/call_hangup.py — CHANNEL_HANGUP_COMPLETE event handler.

This is the financial finalization point for every call. It must run even if
Redis data is incomplete or the call was never answered. Errors here are logged
and never silently swallowed — a missed CDR is worse than a logged failure.

Telecom rules:
  R-BILL-02: tracings are append-only; never UPDATE a tracing row.
  R-BILL-03: CDR must be written even if credit deduction fails.
  R-BILL-05: If call session is missing (worker crash/restart), use ESL event
             data directly to write a best-effort CDR.
  R-BILL-06: Ceiling rounding — partial seconds billed as full seconds.
"""

import math
from datetime import datetime, UTC, timezone
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import CreditDeductionError, InsufficientCreditError, RedisSessionNotFoundError
from app.models.db.tracings import TracingStatus
from app.services.credit_service import (
    CALL_SESSION_KEY,
    CONCURRENT_CALLS_KEY,
    CREDIT_KEY,
    CREDIT_SCALE,
    atomic_deduct_credit,
)
from billing_worker.services.cdr_service import (
    append_tracing,
    finalize_statistics,
    update_consultant_status,
)

log = structlog.get_logger(__name__)
_settings = get_settings()


def _parse_epoch_us(value: str | None) -> datetime | None:
    """Convert FreeSWITCH microsecond epoch string to UTC datetime, or None."""
    if not value or value == "0":
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _parse_epoch_s(value: str | None) -> datetime | None:
    """Convert FreeSWITCH second epoch string to UTC datetime, or None."""
    if not value or value == "0":
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OSError):
        return None


async def handle_hangup(event: dict[str, Any], redis: aioredis.Redis) -> None:
    """
    Handle CHANNEL_HANGUP_COMPLETE: finalize CDR, reconcile credit, reset consultant.

    Steps:
      1. Read call session from Redis (rate, statistics_id, consultant_id, etc.)
      2. Parse timing fields from FreeSWITCH event variables
      3. Compute actual cost (ceiling per second — R-BILL-06)
      4. Reconcile: refund any over-deducted credit back to Redis (short calls)
      5. Finalize statistics row in PostgreSQL (R-BILL-03)
      6. Append HANGUP_TIME and END_TIME tracing rows (R-BILL-02)
      7. Reset consultant ivr_status to online (1)
      8. Delete call session from Redis
    """
    call_uuid = event.get("Unique-ID")
    if not call_uuid:
        log.warning("hangup_missing_uuid", event_keys=list(event.keys()))
        return

    bound_log = log.bind(call_uuid=call_uuid, component="billing_worker")

    # ── 1. Load call session ──────────────────────────────────────────────────
    session_key = CALL_SESSION_KEY.format(call_uuid=call_uuid)
    session: dict[str, str] = await redis.hgetall(session_key)

    if not session:
        # R-BILL-05: session absent — worker restarted and missed CHANNEL_CREATE.
        # Write a minimal CDR from ESL event variables and return.
        bound_log.warning("hangup_no_session_best_effort")
        await _write_best_effort_cdr(event, bound_log)
        return

    account_id = int(session["account_id"])
    rate_per_second = Decimal(session["rate_per_second"])
    rate_per_minute = Decimal(session["rate_per_minute"])
    total_deducted_units = int(session.get("total_deducted_units", "0"))
    statistics_id_str = session.get("statistics_id", "")
    consultant_id_str = session.get("consultant_id", "")
    credit_before = Decimal(session.get("credit_before", "0"))

    # ── 2. Parse FSL event variables ─────────────────────────────────────────
    billsec = int(event.get("variable_billsec", 0))
    duration = int(event.get("variable_duration", 0))
    hangup_cause = event.get("Hangup-Cause", "NORMAL_CLEARING")

    # FS sends timestamps as microseconds since epoch
    answer_time_us = _parse_epoch_us(event.get("Caller-Channel-Answered-Time"))
    hangup_time_us = _parse_epoch_us(event.get("Caller-Channel-Hangup-Time"))
    end_time_us    = _parse_epoch_us(event.get("Caller-Channel-Resurrect-Time")) \
                  or _parse_epoch_us(event.get("Event-Date-Timestamp")) \
                  or datetime.now(UTC)

    # Fallback: use answer_time from Redis session if FS field is zero
    if answer_time_us is None and session.get("answer_time"):
        try:
            answer_time_us = datetime.fromisoformat(session["answer_time"])
        except ValueError:
            pass

    hangup_time_final = hangup_time_us or end_time_us
    conversation_duration = billsec  # FS billsec = answered seconds

    bound_log.info(
        "hangup_received",
        account_id=account_id,
        hangup_cause=hangup_cause,
        billsec=billsec,
        duration=duration,
    )

    # ── 3. Compute actual cost (R-BILL-06: ceiling) ───────────────────────────
    actual_cost_units = math.ceil(float(rate_per_second) * billsec * CREDIT_SCALE)
    credit_cost = Decimal(actual_cost_units) / CREDIT_SCALE
    credit_after = max(Decimal("0"), credit_before - credit_cost)

    # ── 4. Reconcile: refund over-deduction for short calls ───────────────────
    # If actual cost < total provisionally deducted (block + ticks), refund delta.
    over_deducted_units = total_deducted_units - actual_cost_units
    if over_deducted_units > 0:
        credit_key = CREDIT_KEY.format(account_id=account_id)
        try:
            await redis.incrby(credit_key, over_deducted_units)
            bound_log.info(
                "credit_refund_applied",
                over_deducted_units=over_deducted_units,
                actual_cost_units=actual_cost_units,
                total_deducted_units=total_deducted_units,
            )
        except Exception as exc:
            bound_log.error(
                "credit_refund_failed",
                error=str(exc),
                over_deducted_units=over_deducted_units,
            )
            # R-BILL-03: CDR write continues even if credit reconciliation fails

    # ── 5 & 6 & 7. DB operations ──────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        if statistics_id_str:
            statistics_id = int(statistics_id_str)
            try:
                await finalize_statistics(
                    db=db,
                    statistics_id=statistics_id,
                    hangup_time=hangup_time_final,
                    end_time=end_time_us or end_time_us or hangup_time_final,
                    connected_time=answer_time_us,
                    total_duration=duration,
                    conversation_duration=conversation_duration,
                    hangup_cause=hangup_cause,
                    billsec=billsec,
                    credit_after=credit_after,
                    rate_per_second=rate_per_second,
                    short_call_threshold=_settings.short_call_threshold_seconds,
                )
                await append_tracing(
                    db,
                    statistics_id=statistics_id,
                    status=TracingStatus.HANGUP_TIME,
                    timestamp=hangup_time_final,
                    info=hangup_cause,
                    credit_before=credit_before,
                    credit_after=credit_after,
                )
                await append_tracing(
                    db,
                    statistics_id=statistics_id,
                    status=TracingStatus.END_TIME,
                    timestamp=end_time_us or hangup_time_final,
                    info=f"billsec={billsec}",
                )
            except Exception as exc:
                # R-BILL-03: log but never raise — Redis cleanup must still run
                bound_log.error("cdr_write_failed", error=str(exc), exc_info=True)
        else:
            bound_log.warning("hangup_no_statistics_id", account_id=account_id)

        if consultant_id_str:
            try:
                await update_consultant_status(db, int(consultant_id_str), ivr_status=1)
            except Exception as exc:
                bound_log.error("consultant_status_reset_failed", error=str(exc))

    # ── 8. Clean up Redis ─────────────────────────────────────────────────────
    await redis.delete(session_key)

    concurrent_key = CONCURRENT_CALLS_KEY.format(account_id=account_id)
    current = await redis.get(concurrent_key)
    if current and int(current) > 0:
        await redis.decr(concurrent_key)

    bound_log.info(
        "hangup_finalized",
        account_id=account_id,
        billsec=billsec,
        actual_cost_units=actual_cost_units,
        hangup_cause=hangup_cause,
    )


async def _write_best_effort_cdr(event: dict[str, Any], bound_log: Any) -> None:
    """
    Write a minimal CDR when the call session is absent (R-BILL-05 crash recovery).

    We don't have rate/account data so we can only log the ESL event fields.
    A full reconciliation pass (reconcile.py) handles this case on next startup.
    """
    call_uuid = event.get("Unique-ID", "unknown")
    bound_log.warning(
        "best_effort_cdr",
        call_uuid=call_uuid,
        hangup_cause=event.get("Hangup-Cause"),
        billsec=event.get("variable_billsec"),
        caller_id=event.get("Caller-Caller-ID-Number"),
    )
