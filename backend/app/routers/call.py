import uuid
from datetime import datetime, UTC
from decimal import Decimal

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import (
    AccountNotFoundError,
    AccountSuspendedError,
    DIDNotFoundError,
    InsufficientCreditError,
)
from app.dependencies import get_db, get_redis
from app.models.db.statistics import Statistics
from app.models.schemas.call import APIResponse, CallAuthorizeData, CallAuthorizeRequest
from app.services import auth_service, credit_service, routing_service

router = APIRouter(prefix="/v1", tags=["call"])
logger = structlog.get_logger(__name__)
_settings = get_settings()


@router.post("/call/authorize")
async def authorize_call(
    body: CallAuthorizeRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> JSONResponse:
    """
    Authorize an inbound call before FreeSWITCH bridges it.

    Called by Lua dialplan within 2 seconds of SIP INVITE (R-SIP-01).
    This is the most latency-critical endpoint in the system — every DB
    query here must be backed by a Redis cache.

    Combines the legacy Sentinel flow:
      1. POST /api/v1/call/validate  → DID lookup, routing, credit context
      2. POST /api/v1/customer/call/confirm → PIN auth, first block deduction

    account_token = credit_code (8-digit PIN). For pre-authenticated callers
    (ivr_known_users), Lua supplies the PIN from the session; for fresh callers
    Lua obtains it from the customer after DTMF entry, then calls this endpoint.

    On success: creates call session in Redis, deducts first time-block credit.
    On failure: returns structured error — Lua routes to appropriate IVR prompt.

    Telecom note:
        Auth API timeout behavior per R-FLOW-01: if this endpoint does not
        respond within 2000ms, Lua plays the error IVR and hangs up cleanly.
        This endpoint never retries (retries add perceptible delay to call setup).
    """
    request_id = str(uuid.uuid4())
    log = logger.bind(
        request_id=request_id,
        call_uuid=body.call_uuid,
        caller_id=body.caller_id,
        inbound_did=body.inbound_did,
        event_type="call_authorize",
        component="fastapi",
    )

    try:
        # ── 1. Resolve DID ────────────────────────────────────────────────────
        did = await routing_service.lookup_did(db, body.inbound_did)
        log.info("did_resolved", service_type=did.type_id, language_id=did.language_id)

        # ── 2. Resolve consultant for this DID (type 2/4 only) ───────────────
        consultant, phone_number = await routing_service.get_consultant_for_did(db, did.id)

        # ── 3. Check pre-auth status ─────────────────────────────────────────
        known_user = await routing_service.lookup_known_user(db, body.caller_id)
        direct_dial_auth = known_user is not None
        log.info("preauth_check", direct_dial_auth=direct_dial_auth)

        # ── 4. Authenticate customer account ─────────────────────────────────
        customer = await auth_service.get_customer_by_credit_code(
            db, redis, body.account_token
        )

        # ── 5. Determine VAT and FX rate ─────────────────────────────────────
        vat_rate = await auth_service.get_country_vat(db, did.country_id or 0)

        fx_rate = Decimal("1.0")
        if consultant and consultant.currency_code:
            fx_rate = await auth_service.get_fx_rate(
                db, consultant.currency_code, customer.currency_code
            )

        # ── 6. Calculate effective call rate ─────────────────────────────────
        call_rate = Decimal(str(consultant.call_rate)) if consultant else Decimal("0")
        rate_per_minute = credit_service.effective_rate_per_minute(
            call_rate, vat_rate, fx_rate
        )

        # ── 7. Check credit balance ≥ minimum gate (30s) ─────────────────────
        balance_units = await credit_service.get_credit_balance_units(
            redis, customer.id, db, customer
        )
        current_credits = Decimal(balance_units) / credit_service.CREDIT_SCALE
        avb_time = credit_service.calculate_available_time(current_credits, rate_per_minute)

        if avb_time < _settings.credit_min_seconds:
            log.info(
                "call_refused_insufficient_credit",
                account_id=customer.id,
                avb_time_seconds=avb_time,
                min_required=_settings.credit_min_seconds,
            )
            return JSONResponse(
                status_code=402,
                content=APIResponse(
                    success=False,
                    data=None,
                    error="INSUFFICIENT_CREDIT",
                    request_id=request_id,
                ).model_dump(),
            )

        # ── 8. Resolve gateway and destination ───────────────────────────────
        gateway = routing_service.get_gateway_from_consultant(consultant)
        destination_number = (
            phone_number.phone_number
            if phone_number
            else body.dialed_number.lstrip("+")
        )

        # ── 9. Deduct first time-block (like legacy /call/validate deduction) ─
        # Deduct up to 300s worth of credit upfront (Sentinel credit_block_time).
        # Reconciled at hangup — refund unused portion for short calls.
        slot_seconds = min(avb_time, _settings.credit_block_max_seconds)
        slot_units = int(
            Decimal(str(rate_per_minute / 60)) * slot_seconds * credit_service.CREDIT_SCALE
        )
        await credit_service.atomic_deduct_credit(redis, customer.id, slot_units)

        # ── 10. Create statistics stub row (CDR skeleton, finalized at hangup) ─
        # Written now so we have a statistics_id for the billing worker.
        # All nullable fields (connected_time, hangup_time, etc.) are set at hangup.
        stats_stub = Statistics(
            unique_id=body.call_uuid,
            consultant_id=consultant.id if consultant else None,
            credit_customer_id=customer.id,
            site_ivr_number_id=did.id,
            provider_id=1,
            type_id=did.type_id,
            type="call",
            src_number=body.caller_id.lstrip("+"),
            dst_number=destination_number,
            extension=0,
            start_time=datetime.now(UTC).replace(tzinfo=None),
            credit_before=current_credits,
            credit_after=current_credits,  # placeholder — updated at hangup
            coach_rate=call_rate,
            vat_rate=vat_rate,
            customer_currency_code=customer.currency_code,
            customer_currency_rate=Decimal("1.0"),
            coach_currency_code=consultant.currency_code if consultant else "eur",
            coach_currency_rate=fx_rate,
            consultant_earning_for_minute=rate_per_minute,
        )
        db.add(stats_stub)
        await db.flush()   # populate stats_stub.id before commit
        statistics_id = stats_stub.id
        await db.commit()

        # ── 11. Create call session in Redis ──────────────────────────────────
        await credit_service.create_call_session(
            redis=redis,
            call_uuid=body.call_uuid,
            account_id=customer.id,
            rate_per_minute=rate_per_minute,
            gateway=gateway,
            service_type=did.type_id,
            site_ivr_number_id=did.id,
            consultant_id=consultant.id if consultant else None,
            statistics_id=statistics_id,
            credit_before=current_credits,
            initial_deducted_units=slot_units,
        )

        log.info(
            "call_authorized",
            account_id=customer.id,
            avb_time_seconds=avb_time,
            slot_seconds=slot_seconds,
            gateway=gateway,
            service_type=did.type_id,
        )

        return JSONResponse(
            status_code=200,
            content=APIResponse(
                success=True,
                data=CallAuthorizeData(
                    authorized=True,
                    account_id=customer.id,
                    gateway=gateway,
                    destination_number=destination_number,
                    max_duration_seconds=avb_time,
                    rate_per_minute=float(rate_per_minute),
                    currency=customer.currency_code,
                    service_type=did.type_id,
                    call_uuid=body.call_uuid,
                ).model_dump(),
                error=None,
                request_id=request_id,
            ).model_dump(),
        )

    except DIDNotFoundError as exc:
        log.warning("did_not_found", error=str(exc))
        return JSONResponse(
            status_code=404,
            content=APIResponse(
                success=False, data=None, error="DID_NOT_FOUND", request_id=request_id
            ).model_dump(),
        )

    except AccountNotFoundError as exc:
        log.warning("account_not_found", error=str(exc))
        return JSONResponse(
            status_code=401,
            content=APIResponse(
                success=False, data=None, error="ACCOUNT_NOT_FOUND", request_id=request_id
            ).model_dump(),
        )

    except AccountSuspendedError as exc:
        log.warning("account_suspended", error=str(exc))
        return JSONResponse(
            status_code=403,
            content=APIResponse(
                success=False, data=None, error="ACCOUNT_SUSPENDED", request_id=request_id
            ).model_dump(),
        )

    except InsufficientCreditError as exc:
        log.warning("insufficient_credit", error=str(exc))
        return JSONResponse(
            status_code=402,
            content=APIResponse(
                success=False, data=None, error="INSUFFICIENT_CREDIT", request_id=request_id
            ).model_dump(),
        )

    except Exception as exc:
        log.error("authorize_unexpected_error", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=APIResponse(
                success=False, data=None, error="INTERNAL_ERROR", request_id=request_id
            ).model_dump(),
        )
