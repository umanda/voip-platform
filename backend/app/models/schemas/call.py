import uuid as uuid_module
from typing import Any

from pydantic import BaseModel, Field


class APIResponse(BaseModel):
    """
    Standard response envelope for all endpoints.

    Every response from this API wraps data in this structure.
    The Lua HTTP client reads the top-level success flag first,
    then unpacks data on success or logs error on failure.
    """

    success: bool
    data: Any | None = None
    error: str | None = None
    request_id: str = Field(default_factory=lambda: str(uuid_module.uuid4()))


class CallAuthorizeRequest(BaseModel):
    """
    Request from Lua dialplan to authorize an inbound call.

    Sent immediately after SIP INVITE — must be processed within 2000ms (R-SIP-01).

    Legacy mapping: combines POST /api/v1/call/validate + pre-auth credit check.

    caller_id: E.164 caller number (SIP From header), '+' may be present.
    dialed_number: E.164 destination (coach's phone), for routing context.
    inbound_did: E.164 Voxbone DID that received the call — the lookup key.
    account_token: customer's credit_code (8-digit PIN) from ivr_known_users
                   pre-auth or passed from SIP X-Auth header.
    call_uuid: FreeSWITCH UUID — set by FS before Lua runs, used as Redis session key.
    """

    caller_id: str
    dialed_number: str
    inbound_did: str
    account_token: str
    call_uuid: str


class CallAuthorizeData(BaseModel):
    """Authorization success payload returned to Lua."""

    authorized: bool
    account_id: int
    gateway: str                  # FreeSWITCH gateway name: sofia/gateway/<gateway>/
    destination_number: str       # Coach's PSTN number for outbound dial (no '+')
    max_duration_seconds: int
    rate_per_minute: float
    currency: str
    service_type: int             # 1=site, 2=direct, 3=SD, 4=coach
    call_uuid: str
