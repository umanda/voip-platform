import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RedisSessionNotFoundError
from app.dependencies import get_db, get_redis
from app.models.schemas.billing import BillingTickRequest
from app.models.schemas.call import APIResponse
from app.services import credit_service

router = APIRouter(prefix="/v1", tags=["billing"])
logger = structlog.get_logger(__name__)

# Fail-safe response returned whenever the tick cannot complete.
# Lua reads continue=false and immediately hangs up the call.
_TICK_FAIL_RESPONSE = {
    "continue": False,
    "remaining_seconds": 0,
    "deducted_amount": 0.0,
}


@router.post("/billing/tick")
async def billing_tick(
    body: BillingTickRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> JSONResponse:
    """
    Deduct credit for an elapsed billing period during an active call.

    Called by Lua every 60 seconds. Must respond within 2000ms (R-SIP-01).

    Maps to legacy: POST /api/v1/check/call/time.
    Key difference: legacy used pre-deducted time blocks (up to 300s);
    new system deducts continuously per tick — simpler and prevents the
    5-second pre-renewal race condition from the legacy coachSessionHandler.

    If this endpoint returns continue=false, Lua MUST hangup immediately.
    Any error (session missing, Redis down) also returns continue=false —
    it is always safer to end the call than to allow untracked call time.

    Telecom note:
        R-FLOW-02: never abruptly disconnect. Lua should play "low credit"
        prompt when remaining_seconds < credit_warn_threshold (320s) and
        "call ending in 10 seconds" at 10s, before sending BYE.
    """
    request_id = str(uuid.uuid4())
    log = logger.bind(
        request_id=request_id,
        call_uuid=body.call_uuid,
        account_id=body.account_id,
        elapsed_seconds=body.elapsed_seconds,
        event_type="billing_tick",
        component="fastapi",
    )

    try:
        continue_call, remaining_seconds, deducted = await credit_service.process_billing_tick(
            redis=redis,
            call_uuid=body.call_uuid,
            account_id=body.account_id,
            elapsed_seconds=body.elapsed_seconds,
        )

        log.info(
            "billing_tick_processed",
            continue_call=continue_call,
            remaining_seconds=remaining_seconds,
            deducted_amount=deducted,
        )

        return JSONResponse(
            status_code=200,
            content=APIResponse(
                success=True,
                data={
                    "continue": continue_call,
                    "remaining_seconds": remaining_seconds,
                    "deducted_amount": deducted,
                },
                error=None,
                request_id=request_id,
            ).model_dump(),
        )

    except RedisSessionNotFoundError as exc:
        log.error("billing_tick_session_not_found", error=str(exc))
        return JSONResponse(
            status_code=200,
            content=APIResponse(
                success=False,
                data=_TICK_FAIL_RESPONSE,
                error="SESSION_NOT_FOUND",
                request_id=request_id,
            ).model_dump(),
        )

    except Exception as exc:
        log.error("billing_tick_unexpected_error", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=200,
            content=APIResponse(
                success=False,
                data=_TICK_FAIL_RESPONSE,
                error="INTERNAL_ERROR",
                request_id=request_id,
            ).model_dump(),
        )
