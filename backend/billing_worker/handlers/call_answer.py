"""
billing_worker/handlers/call_answer.py — CHANNEL_ANSWER event handler.

Sets statistics.connected_time and marks the consultant as busy (ivr_status=2).

Telecom note:
    CHANNEL_ANSWER fires when the B-leg (consultant) picks up the phone.
    This is the moment billing seconds start counting (billsec in FreeSWITCH).
    The ivr_status=2 flag prevents the IVR from routing new calls to this
    consultant while they're already in a call.
"""

from datetime import datetime, UTC
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services.credit_service import CALL_SESSION_KEY
from billing_worker.services.cdr_service import append_tracing, set_connected_time, update_consultant_status
from app.models.db.tracings import TracingStatus

log = structlog.get_logger(__name__)


async def handle_answer(event: dict[str, Any], redis: aioredis.Redis) -> None:
    """
    Handle CHANNEL_ANSWER: record answer time, mark consultant busy.

    Steps:
      1. Read call session from Redis (get statistics_id, consultant_id)
      2. Update statistics.connected_time
      3. Insert CONNECTED_TIME tracing row
      4. Set consultant ivr_status = 2 (busy)
      5. Store answer_time in Redis session (fallback for hangup reconciliation)
    """
    call_uuid = event.get("Unique-ID")
    if not call_uuid:
        return

    session_key = CALL_SESSION_KEY.format(call_uuid=call_uuid)
    session = await redis.hgetall(session_key)
    if not session:
        # Not one of our managed calls
        return

    answer_time = datetime.now(UTC)

    # Store answer_time in Redis for hangup reconciliation fallback
    await redis.hset(session_key, "answer_time", answer_time.isoformat())

    statistics_id_str = session.get("statistics_id", "")
    consultant_id_str = session.get("consultant_id", "")

    if not statistics_id_str:
        log.warning("answer_no_statistics_id", call_uuid=call_uuid)
        return

    statistics_id = int(statistics_id_str)

    async with AsyncSessionLocal() as db:
        await set_connected_time(db, statistics_id, answer_time)
        await append_tracing(
            db,
            statistics_id=statistics_id,
            status=TracingStatus.CONNECTED_TIME,
            timestamp=answer_time,
        )
        if consultant_id_str:
            await update_consultant_status(db, int(consultant_id_str), ivr_status=2)

    log.info(
        "call_answered",
        call_uuid=call_uuid,
        statistics_id=statistics_id,
        consultant_id=consultant_id_str or None,
    )
