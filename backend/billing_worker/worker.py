"""
Billing Worker — FreeSWITCH ESL event consumer.

Subscribes to FreeSWITCH call events and finalizes CDRs on hangup.
Replaces the synchronous hangup HTTP handler in the legacy checkServiceType.pl.

Key improvements over legacy:
  - Async: never blocks a FreeSWITCH dialplan thread
  - Retry-safe: ESL events are idempotent; re-processing a hangup is safe
  - Crash-recovery: R-BILL-05 reconciliation on startup
  - CDR persistence: Redis first, Postgres async (R-BILL-03)

Run as: python -m billing_worker.worker
"""

import asyncio

import structlog

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import get_redis
from billing_worker.esl.client import ESLClient
from billing_worker.handlers.call_answer import handle_answer
from billing_worker.handlers.call_hangup import handle_hangup
from billing_worker.handlers.reconcile import reconcile_on_startup

logger = structlog.get_logger(__name__)
_settings = get_settings()

SUBSCRIBED_EVENTS = [
    "CHANNEL_HANGUP_COMPLETE",
    "CHANNEL_ANSWER",
    "CHANNEL_CREATE",
]


async def event_loop(esl: ESLClient, redis) -> None:
    """
    Main event dispatch loop.

    Reads ESL events indefinitely and dispatches to async task handlers.
    Tasks run concurrently — a slow DB write does not block the next event.
    Each handler is wrapped in its own task so a slow CDR write cannot delay
    the next ESL event read.
    """
    await esl.subscribe(SUBSCRIBED_EVENTS)
    logger.info("billing_worker_listening", events=SUBSCRIBED_EVENTS)

    while esl.is_connected:
        event = await esl.read_event()
        if event is None:
            break

        event_name = event.get("Event-Name")

        if event_name == "CHANNEL_HANGUP_COMPLETE":
            asyncio.create_task(handle_hangup(event, redis))
        elif event_name == "CHANNEL_ANSWER":
            asyncio.create_task(handle_answer(event, redis))
        # CHANNEL_CREATE: call session created at authorize; no action needed


async def run_worker() -> None:
    """
    Worker entry point with exponential-backoff ESL reconnection.

    On each connection cycle:
      1. Connect to FreeSWITCH ESL
      2. Run startup reconciliation (find orphaned sessions from last crash)
      3. Enter event loop until disconnected
      4. Wait and reconnect (backoff doubles each failure, capped at 60s)

    Telecom constraint (R-INFRA-05): This worker must run as a single instance
    (ECS desired_count=1) to avoid duplicate CDR writes. The reconcile handler
    makes startup idempotent so a rolling replacement is safe.
    """
    configure_logging(debug=_settings.debug)
    logger.info(
        "billing_worker_starting",
        esl_host=_settings.freeswitch_esl_host,
        esl_port=_settings.freeswitch_esl_port,
    )

    redis = await get_redis()
    backoff = 5

    while True:
        esl = ESLClient(
            host=_settings.freeswitch_esl_host,
            port=_settings.freeswitch_esl_port,
            password=_settings.freeswitch_esl_password,
        )
        try:
            await esl.connect()
            await reconcile_on_startup(esl, redis)
            await event_loop(esl, redis)
        except (ConnectionRefusedError, OSError) as exc:
            logger.error("esl_connection_failed", error=str(exc), retry_in_seconds=backoff)
        except Exception as exc:
            logger.error("worker_unexpected_error", error=str(exc), exc_info=True)
        finally:
            await esl.disconnect()

        logger.info("worker_reconnecting", backoff_seconds=backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(run_worker())
