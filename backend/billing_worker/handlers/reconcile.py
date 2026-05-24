"""
billing_worker/handlers/reconcile.py — Startup crash recovery (R-BILL-05).

On worker startup, any Redis call sessions that don't correspond to a live
FreeSWITCH call are orphaned — the worker died mid-call and missed the
CHANNEL_HANGUP_COMPLETE event. This handler synthesizes a hangup event for each
orphaned session so the CDR is finalized and the consultant status is reset.

Also resets any consultant stuck at ivr_status=2 (busy) with no active session,
preventing permanently unavailable consultants after a worker crash (HIGH-04).

Migration notes:
  Legacy: Sentinel had no crash recovery — crashed sessions left consultants
          stuck as busy until an admin manually reset ivr_status.
  New:    This handler runs on every worker start (safe to re-run; idempotent
          because handle_hangup deletes the Redis session key).
"""

import structlog
from datetime import datetime, UTC

import redis.asyncio as aioredis
from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal
from app.models.db.consultants import Consultant
from app.services.credit_service import CALL_SESSION_KEY
from billing_worker.esl.client import ESLClient
from billing_worker.handlers.call_hangup import handle_hangup

log = structlog.get_logger(__name__)


async def reconcile_on_startup(esl: ESLClient, redis: aioredis.Redis) -> None:
    """
    Detect and finalize orphaned call sessions from a previous worker run.

    Steps:
      1. Scan Redis for all call:{uuid} keys
      2. Query FreeSWITCH ESL "show calls as json" for live call UUIDs
      3. Any Redis session UUID not in the live set is orphaned
      4. Synthesize a WORKER_RESTART hangup event and call handle_hangup
      5. Reset any consultant with ivr_status=2 but no active Redis session

    This is idempotent: handle_hangup deletes the session key on completion,
    so re-running reconcile on a fully-reconciled set is a no-op.
    """
    log.info("reconciliation_starting")

    # ── 1. All active Redis sessions ─────────────────────────────────────────
    session_keys: list[bytes] = await redis.keys("call:*")
    if not session_keys:
        log.info("reconciliation_complete", orphaned_sessions=0)
        await _reset_stuck_consultants(redis)
        return

    # ── 2. Live FreeSWITCH calls ──────────────────────────────────────────────
    live_uuids: set[str] = set()
    try:
        output = await esl.execute_api("show calls")
        for line in output.splitlines():
            parts = line.strip().split()
            if parts and len(parts[0]) == 36 and parts[0].count("-") == 4:
                live_uuids.add(parts[0])
    except Exception as exc:
        log.error("reconcile_esl_query_failed", error=str(exc))
        # Continue with empty live set — safer to over-reconcile than to skip
        live_uuids = set()

    # ── 3. Find orphaned sessions ─────────────────────────────────────────────
    orphaned_uuids: list[str] = []
    for raw_key in session_keys:
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        call_uuid = key.removeprefix("call:")
        if call_uuid not in live_uuids:
            orphaned_uuids.append(call_uuid)

    log.info("reconciliation_scan_complete",
             total_sessions=len(session_keys),
             live_calls=len(live_uuids),
             orphaned=len(orphaned_uuids))

    # ── 4. Synthesize hangup for each orphaned session ────────────────────────
    for call_uuid in orphaned_uuids:
        log.warning("orphaned_session_detected", call_uuid=call_uuid)
        synthetic_event = {
            "Unique-ID": call_uuid,
            "Hangup-Cause": "WORKER_RESTART",
            "variable_billsec": "0",
            "variable_duration": "0",
            "Caller-Channel-Answered-Time": "0",
            "Caller-Channel-Hangup-Time": "0",
        }
        try:
            await handle_hangup(synthetic_event, redis)
        except Exception as exc:
            log.error("orphaned_session_reconcile_failed",
                      call_uuid=call_uuid, error=str(exc))

    log.info("reconciliation_complete", orphaned_sessions=len(orphaned_uuids))

    # ── 5. Reset stuck consultants ────────────────────────────────────────────
    await _reset_stuck_consultants(redis)


async def _reset_stuck_consultants(redis: aioredis.Redis) -> None:
    """
    Reset consultants with ivr_status=2 (busy) but no active Redis call session.

    This handles the HIGH-04 risk: consultant stuck as busy after a worker crash.
    We collect all account_ids from remaining call sessions, then reset any
    consultant whose session is gone but ivr_status is still 2.
    """
    # Get all active session account_ids
    active_keys: list[bytes] = await redis.keys("call:*")
    active_account_ids: set[int] = set()
    for raw_key in active_keys:
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        call_uuid = key.removeprefix("call:")
        session_key = CALL_SESSION_KEY.format(call_uuid=call_uuid)
        session = await redis.hgetall(session_key)
        if session and session.get("consultant_id"):
            try:
                active_account_ids.add(int(session["consultant_id"]))
            except (ValueError, KeyError):
                pass

    async with AsyncSessionLocal() as db:
        # Find consultants stuck as busy (ivr_status=2)
        result = await db.execute(
            select(Consultant.id).where(Consultant.ivr_status == 2)
        )
        stuck_ids = [row[0] for row in result.fetchall()]

        reset_count = 0
        for consultant_id in stuck_ids:
            if consultant_id not in active_account_ids:
                await db.execute(
                    update(Consultant)
                    .where(Consultant.id == consultant_id)
                    .values(ivr_status=1)
                )
                reset_count += 1

        if reset_count:
            await db.commit()

    if reset_count:
        log.warning("stuck_consultants_reset", count=reset_count)
    else:
        log.info("no_stuck_consultants")
