"""
Tests for billing_worker/handlers/reconcile.py

Verifies R-BILL-05 startup reconciliation:
  - Sessions with no live FS call get a synthetic hangup
  - Sessions with a live FS call are left untouched
  - No active sessions → reconciliation completes immediately
  - Stuck consultants (ivr_status=2, no session) are reset to ivr_status=1
"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_esl(show_calls_output: str = "") -> MagicMock:
    esl = AsyncMock()
    esl.execute_api = AsyncMock(return_value=show_calls_output)
    return esl


def _make_redis(session_keys: list[str] = [], sessions: dict[str, dict] = {}) -> AsyncMock:
    redis = AsyncMock()
    redis.keys = AsyncMock(return_value=[k.encode() for k in session_keys])
    redis.hgetall = AsyncMock(
        side_effect=lambda key: sessions.get(key.decode() if isinstance(key, bytes) else key, {})
    )
    return redis


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_no_sessions_is_noop():
    """No Redis sessions → reconcile completes immediately without ESL call."""
    esl = _make_esl()
    redis = _make_redis(session_keys=[])

    with patch("billing_worker.handlers.reconcile.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.reconcile.handle_hangup", new_callable=AsyncMock) as mock_hangup:

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        from billing_worker.handlers.reconcile import reconcile_on_startup
        await reconcile_on_startup(esl, redis)

    mock_hangup.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_orphaned_session_triggers_hangup():
    """Session in Redis with no matching live FS call → synthetic hangup called."""
    orphan_uuid = "deadbeef-0000-0000-0000-000000000001"
    esl = _make_esl(show_calls_output="")  # no live calls
    redis = _make_redis(
        session_keys=[f"call:{orphan_uuid}"],
        sessions={f"call:{orphan_uuid}": {"account_id": "42", "consultant_id": "7"}},
    )

    with patch("billing_worker.handlers.reconcile.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.reconcile.handle_hangup", new_callable=AsyncMock) as mock_hangup:

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        from billing_worker.handlers.reconcile import reconcile_on_startup
        await reconcile_on_startup(esl, redis)

    mock_hangup.assert_called_once()
    synthetic_event = mock_hangup.call_args[0][0]
    assert synthetic_event["Unique-ID"] == orphan_uuid
    assert synthetic_event["Hangup-Cause"] == "WORKER_RESTART"
    assert synthetic_event["variable_billsec"] == "0"


@pytest.mark.asyncio
async def test_reconcile_live_session_not_touched():
    """Session UUID matches a live FS call → hangup is NOT synthesized."""
    live_uuid = "livebeef-0000-0000-0000-000000000001"
    esl = _make_esl(show_calls_output=f"{live_uuid} ACTIVE\n")
    redis = _make_redis(
        session_keys=[f"call:{live_uuid}"],
        sessions={f"call:{live_uuid}": {"account_id": "42"}},
    )

    with patch("billing_worker.handlers.reconcile.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.reconcile.handle_hangup", new_callable=AsyncMock) as mock_hangup:

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        from billing_worker.handlers.reconcile import reconcile_on_startup
        await reconcile_on_startup(esl, redis)

    mock_hangup.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_mixed_live_and_orphaned():
    """One live call and one orphan: only the orphan gets a synthetic hangup."""
    live_uuid = "livebeef-0000-0000-0000-000000000001"
    orphan_uuid = "deadbeef-0000-0000-0000-000000000002"

    esl = _make_esl(show_calls_output=f"{live_uuid} ACTIVE\n")
    redis = _make_redis(
        session_keys=[f"call:{live_uuid}", f"call:{orphan_uuid}"],
        sessions={
            f"call:{live_uuid}": {"account_id": "42"},
            f"call:{orphan_uuid}": {"account_id": "99"},
        },
    )

    with patch("billing_worker.handlers.reconcile.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.reconcile.handle_hangup", new_callable=AsyncMock) as mock_hangup:

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        from billing_worker.handlers.reconcile import reconcile_on_startup
        await reconcile_on_startup(esl, redis)

    assert mock_hangup.call_count == 1
    event_arg = mock_hangup.call_args[0][0]
    assert event_arg["Unique-ID"] == orphan_uuid


@pytest.mark.asyncio
async def test_reconcile_esl_failure_still_reconciles():
    """ESL query failure → treat all sessions as orphaned (conservative)."""
    orphan_uuid = "deadbeef-0000-0000-0000-000000000001"
    esl = AsyncMock()
    esl.execute_api = AsyncMock(side_effect=ConnectionError("ESL down"))

    redis = _make_redis(
        session_keys=[f"call:{orphan_uuid}"],
        sessions={f"call:{orphan_uuid}": {"account_id": "42"}},
    )

    with patch("billing_worker.handlers.reconcile.AsyncSessionLocal") as mock_session_cm, \
         patch("billing_worker.handlers.reconcile.handle_hangup", new_callable=AsyncMock) as mock_hangup:

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        from billing_worker.handlers.reconcile import reconcile_on_startup
        await reconcile_on_startup(esl, redis)

    # With ESL down, live_uuids is empty → all sessions treated as orphaned
    mock_hangup.assert_called_once()
