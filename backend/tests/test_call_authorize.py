"""
Tests for POST /v1/call/authorize.

Required by 02-build-fastapi.md before Phase 2 is considered complete.
All tests mock DB and Redis — no real infrastructure needed.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AccountNotFoundError, AccountSuspendedError, DIDNotFoundError
from app.models.db.consultants import Consultant
from app.models.db.credits_customers import CreditsCustomer
from app.models.db.site_ivr_numbers import SiteIvrNumber
from app.services.credit_service import CREDIT_SCALE

AUTHORIZE_URL = "/v1/call/authorize"

BASE_PAYLOAD = {
    "caller_id": "+94771234567",
    "dialed_number": "+33612345678",
    "inbound_did": "+442071234567",
    "account_token": "12345678",
    "call_uuid": "test-uuid-abcd-1234",
}


def _make_did(type_id: int = 2) -> MagicMock:
    did = MagicMock(spec=SiteIvrNumber)
    did.id = 1
    did.number = "442071234567"
    did.type_id = type_id
    did.language_id = 4
    did.country_id = None
    did.group_id = 3
    return did


def _make_consultant(call_rate: Decimal = Decimal("1.20")) -> MagicMock:
    c = MagicMock(spec=Consultant)
    c.id = 42
    c.call_rate = call_rate
    c.currency_code = "eur"
    c.provider_sequence = "voxbone-outbound"
    c.ivr_status = 1
    c.is_blocked = False
    c.is_deleted = False
    return c


def _make_customer(credits: Decimal = Decimal("10.00000")) -> MagicMock:
    c = MagicMock(spec=CreditsCustomer)
    c.id = 101
    c.user_id = 55
    c.credit_code = "12345678"
    c.current_credits = credits
    c.currency_code = "eur"
    c.is_blocked = False
    c.is_deleted = False
    return c


# ── Success path ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_success(async_client, mock_redis):
    """Valid token + sufficient credit → 200 with authorized=True."""
    balance_units = int(Decimal("10") * CREDIT_SCALE)  # 10 EUR

    with (
        patch("app.routers.call.routing_service.lookup_did", return_value=_make_did()),
        patch(
            "app.routers.call.routing_service.get_consultant_for_did",
            return_value=(_make_consultant(), None),
        ),
        patch("app.routers.call.routing_service.lookup_known_user", return_value=None),
        patch(
            "app.routers.call.auth_service.get_customer_by_credit_code",
            return_value=_make_customer(),
        ),
        patch(
            "app.routers.call.auth_service.get_country_vat",
            return_value=Decimal("20"),
        ),
        patch("app.routers.call.auth_service.get_fx_rate", return_value=Decimal("1.0")),
        patch(
            "app.routers.call.credit_service.get_credit_balance_units",
            return_value=balance_units,
        ),
        patch(
            "app.routers.call.credit_service.atomic_deduct_credit",
            return_value=balance_units - 100,
        ),
        patch("app.routers.call.credit_service.create_call_session", new_callable=AsyncMock),
    ):
        response = await async_client.post(AUTHORIZE_URL, json=BASE_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["authorized"] is True
    assert body["data"]["call_uuid"] == BASE_PAYLOAD["call_uuid"]
    assert body["data"]["account_id"] == 101
    assert "request_id" in body
    assert body["error"] is None


# ── Failure paths ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_insufficient_credit(async_client):
    """Balance below 30-second minimum → 402 INSUFFICIENT_CREDIT."""
    # 1 unit = 0.00001 credit — effectively zero
    tiny_balance = 1

    with (
        patch("app.routers.call.routing_service.lookup_did", return_value=_make_did()),
        patch(
            "app.routers.call.routing_service.get_consultant_for_did",
            return_value=(None, None),
        ),
        patch("app.routers.call.routing_service.lookup_known_user", return_value=None),
        patch(
            "app.routers.call.auth_service.get_customer_by_credit_code",
            return_value=_make_customer(Decimal("0.00001")),
        ),
        patch(
            "app.routers.call.auth_service.get_country_vat",
            return_value=Decimal("20"),
        ),
        patch("app.routers.call.auth_service.get_fx_rate", return_value=Decimal("1.0")),
        patch(
            "app.routers.call.credit_service.get_credit_balance_units",
            return_value=tiny_balance,
        ),
    ):
        response = await async_client.post(AUTHORIZE_URL, json=BASE_PAYLOAD)

    assert response.status_code == 402
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "INSUFFICIENT_CREDIT"


@pytest.mark.asyncio
async def test_authorize_invalid_token(async_client):
    """Unknown credit_code → 401 ACCOUNT_NOT_FOUND."""
    with (
        patch("app.routers.call.routing_service.lookup_did", return_value=_make_did()),
        patch(
            "app.routers.call.routing_service.get_consultant_for_did",
            return_value=(None, None),
        ),
        patch("app.routers.call.routing_service.lookup_known_user", return_value=None),
        patch(
            "app.routers.call.auth_service.get_customer_by_credit_code",
            side_effect=AccountNotFoundError("no account"),
        ),
    ):
        response = await async_client.post(AUTHORIZE_URL, json=BASE_PAYLOAD)

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_authorize_suspended_account(async_client):
    """Blocked account → 403 ACCOUNT_SUSPENDED."""
    with (
        patch("app.routers.call.routing_service.lookup_did", return_value=_make_did()),
        patch(
            "app.routers.call.routing_service.get_consultant_for_did",
            return_value=(None, None),
        ),
        patch("app.routers.call.routing_service.lookup_known_user", return_value=None),
        patch(
            "app.routers.call.auth_service.get_customer_by_credit_code",
            side_effect=AccountSuspendedError("blocked"),
        ),
    ):
        response = await async_client.post(AUTHORIZE_URL, json=BASE_PAYLOAD)

    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "ACCOUNT_SUSPENDED"


@pytest.mark.asyncio
async def test_authorize_did_not_found(async_client):
    """Unknown DID → 404 DID_NOT_FOUND."""
    with patch(
        "app.routers.call.routing_service.lookup_did",
        side_effect=DIDNotFoundError("not found"),
    ):
        response = await async_client.post(AUTHORIZE_URL, json=BASE_PAYLOAD)

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "DID_NOT_FOUND"


# ── Response shape ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_response_has_required_fields(async_client):
    """Success response includes all fields Lua dialplan depends on."""
    balance_units = int(Decimal("10") * CREDIT_SCALE)

    with (
        patch("app.routers.call.routing_service.lookup_did", return_value=_make_did()),
        patch(
            "app.routers.call.routing_service.get_consultant_for_did",
            return_value=(_make_consultant(), None),
        ),
        patch("app.routers.call.routing_service.lookup_known_user", return_value=None),
        patch(
            "app.routers.call.auth_service.get_customer_by_credit_code",
            return_value=_make_customer(),
        ),
        patch(
            "app.routers.call.auth_service.get_country_vat",
            return_value=Decimal("20"),
        ),
        patch("app.routers.call.auth_service.get_fx_rate", return_value=Decimal("1.0")),
        patch(
            "app.routers.call.credit_service.get_credit_balance_units",
            return_value=balance_units,
        ),
        patch(
            "app.routers.call.credit_service.atomic_deduct_credit",
            return_value=balance_units - 100,
        ),
        patch("app.routers.call.credit_service.create_call_session", new_callable=AsyncMock),
    ):
        response = await async_client.post(AUTHORIZE_URL, json=BASE_PAYLOAD)

    data = response.json()["data"]
    required_fields = [
        "authorized", "account_id", "gateway", "destination_number",
        "max_duration_seconds", "rate_per_minute", "currency", "service_type", "call_uuid",
    ]
    for field in required_fields:
        assert field in data, f"Missing field: {field}"
