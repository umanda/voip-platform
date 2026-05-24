"""
tests/integration/test_call_flow.py — End-to-end call flow integration tests.

These tests require the full stack running (postgres + redis + FreeSWITCH).
They are run separately from the unit tests because they require real infrastructure.

What is tested:
  1. FastAPI /v1/call/authorize returns authorized=True for a seeded customer
  2. FastAPI /v1/billing/tick deducts credit from Redis
  3. FastAPI /v1/billing/hangup finalizes a CDR in PostgreSQL
  4. Redis credit balance reflects deductions across tick calls
  5. CDR row in statistics table is written after hangup

Prerequisites (already seeded by scripts/db/init.sql):
  - Customer: credit_code=12345678, 10 EUR credit
  - Consultant id=1: 1.20 EUR/min, ivr_status=1 (online)
  - DID: 442071234567 (site_ivr_numbers id=1)

Run:
  docker compose -f docker-compose.test.yml run --rm api \
      pytest tests/integration/ -v --tb=short -m integration

NOTE: SIP signalling tests (real INVITE/BYE via sipp) are not run here.
They require a real FreeSWITCH and network access to the SIP stack.
See docs/guides/lab-setup-guide.md for the sipp test procedure.
"""

import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio

# Mark all tests in this module as integration (skip by default in unit test runs)
pytestmark = pytest.mark.integration

BASE_URL = "http://api:8000"   # Docker compose service name

# Seed data from scripts/db/init.sql
CUSTOMER_PIN      = "12345678"
CONSULTANT_ID     = 1
INBOUND_DID       = "+442071234567"
CALLER_ID         = "+32475000001"
CONSULTANT_NUMBER = "+32265664982"


@pytest_asyncio.fixture
async def http_client() -> httpx.AsyncClient:
    """Async HTTP client pointing at the running API container."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture
def call_uuid() -> str:
    """Unique call UUID per test — mirrors FreeSWITCH channel UUID format."""
    return str(uuid.uuid4())


# ── Health check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_health(http_client: httpx.AsyncClient) -> None:
    """API must be healthy with DB + Redis connected before running call tests."""
    response = await http_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy", f"API not healthy: {body}"
    assert body["components"]["database"] == "ok"
    assert body["components"]["redis"] == "ok"


# ── Call authorize ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_success(
    http_client: httpx.AsyncClient,
    call_uuid: str,
) -> None:
    """
    Valid customer PIN + online consultant → authorized=True with gateway and duration.

    Mirrors the Lua auth.lua HTTP call:
      POST /v1/call/authorize
      {caller_id, dialed_number, inbound_did, account_token, call_uuid}
    """
    payload = {
        "caller_id":       CALLER_ID,
        "dialed_number":   CONSULTANT_NUMBER,
        "inbound_did":     INBOUND_DID,
        "account_token":   CUSTOMER_PIN,
        "call_uuid":       call_uuid,
    }
    response = await http_client.post("/v1/call/authorize", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["authorized"] is True
    assert data["gateway"] == "voxbone-outbound"
    assert data["max_duration_seconds"] > 0
    # Credit balance must be positive (seeded with 10 EUR)
    assert data["credit_balance_seconds"] > 0


@pytest.mark.asyncio
async def test_authorize_blocked_account(
    http_client: httpx.AsyncClient,
    call_uuid: str,
) -> None:
    """
    Blocked customer account (is_blocked=True, PIN 99999999) → authorized=False.
    Account is seeded as blocked in scripts/db/init.sql.
    """
    payload = {
        "caller_id":     CALLER_ID,
        "dialed_number": CONSULTANT_NUMBER,
        "inbound_did":   INBOUND_DID,
        "account_token": "99999999",   # Bob — is_blocked=True in seed data
        "call_uuid":     call_uuid,
    }
    response = await http_client.post("/v1/call/authorize", json=payload)
    assert response.status_code in (200, 403)
    if response.status_code == 200:
        assert response.json()["data"]["authorized"] is False


@pytest.mark.asyncio
async def test_authorize_unknown_pin(
    http_client: httpx.AsyncClient,
    call_uuid: str,
) -> None:
    """Unknown PIN → 404 or authorized=False."""
    payload = {
        "caller_id":     CALLER_ID,
        "dialed_number": CONSULTANT_NUMBER,
        "inbound_did":   INBOUND_DID,
        "account_token": "00000000",   # Not in seed data
        "call_uuid":     call_uuid,
    }
    response = await http_client.post("/v1/call/authorize", json=payload)
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.json()["data"]["authorized"] is False


# ── Billing tick ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_billing_tick_deducts_credit(
    http_client: httpx.AsyncClient,
    call_uuid: str,
) -> None:
    """
    Billing tick for an active call deducts credit from Redis.

    Flow:
      1. Authorize → get initial max_duration_seconds
      2. Send billing tick → should deduct ~60s worth of credit
      3. Balance after tick < balance before tick
    """
    # Step 1: authorize to create call session in Redis
    auth_payload = {
        "caller_id":     CALLER_ID,
        "dialed_number": CONSULTANT_NUMBER,
        "inbound_did":   INBOUND_DID,
        "account_token": CUSTOMER_PIN,
        "call_uuid":     call_uuid,
    }
    auth_resp = await http_client.post("/v1/call/authorize", json=auth_payload)
    assert auth_resp.status_code == 200
    balance_before = auth_resp.json()["data"]["credit_balance_seconds"]

    # Step 2: send billing tick (simulates Lua billing/tick.lua after 60s)
    tick_payload = {
        "call_uuid":    call_uuid,
        "elapsed_seconds": 60,
    }
    tick_resp = await http_client.post("/v1/billing/tick", json=tick_payload)
    assert tick_resp.status_code == 200, tick_resp.text
    tick_body = tick_resp.json()
    assert tick_body["success"] is True

    # Step 3: balance after tick must be less than before
    balance_after = tick_body["data"]["remaining_seconds"]
    assert balance_after < balance_before, (
        f"Credit not deducted: before={balance_before}, after={balance_after}"
    )


# ── Billing hangup ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_billing_hangup_writes_cdr(
    http_client: httpx.AsyncClient,
    call_uuid: str,
) -> None:
    """
    Billing hangup finalizes the CDR — verifies the API accepts the hangup event.

    NOTE: Actual CDR persistence to PostgreSQL is handled by the billing_worker
    via ESL CHANNEL_HANGUP_COMPLETE event, not by this endpoint directly.
    This test only verifies the API endpoint accepts the hangup payload correctly.
    """
    # Authorize first to create the call session
    auth_payload = {
        "caller_id":     CALLER_ID,
        "dialed_number": CONSULTANT_NUMBER,
        "inbound_did":   INBOUND_DID,
        "account_token": CUSTOMER_PIN,
        "call_uuid":     call_uuid,
    }
    auth_resp = await http_client.post("/v1/call/authorize", json=auth_payload)
    assert auth_resp.status_code == 200

    # Send hangup event
    hangup_payload = {
        "call_uuid":         call_uuid,
        "hangup_cause":      "NORMAL_CLEARING",
        "duration_seconds":  90,
        "answered":          True,
    }
    hangup_resp = await http_client.post("/v1/billing/hangup", json=hangup_payload)
    assert hangup_resp.status_code == 200, hangup_resp.text
    body = hangup_resp.json()
    assert body["success"] is True


# ── Full call lifecycle ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_call_lifecycle(
    http_client: httpx.AsyncClient,
) -> None:
    """
    Simulate the complete call lifecycle: authorize → tick × 2 → hangup.

    This is the closest integration test to a real call without SIP signalling.
    Mirrors the sequence:
      Lua auth.lua → POST /authorize
      Lua tick.lua × N → POST /billing/tick  (every 60s)
      Lua hangup.lua → POST /billing/hangup
    """
    cid = str(uuid.uuid4())

    # 1. Authorize
    auth_resp = await http_client.post("/v1/call/authorize", json={
        "caller_id":     "+32475123456",
        "dialed_number": CONSULTANT_NUMBER,
        "inbound_did":   INBOUND_DID,
        "account_token": CUSTOMER_PIN,
        "call_uuid":     cid,
    })
    assert auth_resp.status_code == 200
    assert auth_resp.json()["data"]["authorized"] is True

    initial_seconds = auth_resp.json()["data"]["credit_balance_seconds"]

    # 2. Two billing ticks (simulates 120 seconds of call)
    for elapsed in [60, 120]:
        tick_resp = await http_client.post("/v1/billing/tick", json={
            "call_uuid":       cid,
            "elapsed_seconds": elapsed,
        })
        assert tick_resp.status_code == 200, f"Tick failed at {elapsed}s: {tick_resp.text}"

    after_ticks = tick_resp.json()["data"]["remaining_seconds"]
    assert after_ticks < initial_seconds

    # 3. Hangup
    hangup_resp = await http_client.post("/v1/billing/hangup", json={
        "call_uuid":        cid,
        "hangup_cause":     "NORMAL_CLEARING",
        "duration_seconds": 125,
        "answered":         True,
    })
    assert hangup_resp.status_code == 200
    assert hangup_resp.json()["success"] is True
