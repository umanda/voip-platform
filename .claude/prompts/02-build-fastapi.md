# Prompt: Build FastAPI Backend (Phases 1–2)

## Prerequisites
- Phase 0 audit complete (`docs/legacy-audit/` exists and is reviewed)
- You have reviewed `.claude/context/coding-standards.md`
- You have reviewed `.claude/context/architecture.md`
- You have reviewed `.claude/context/telecom-rules.md`

## Your Role
You are building the FastAPI backend that will replace the PHP Sentinel API.
This service will be called by Lua scripts inside FreeSWITCH.
Every endpoint must respond in under 500ms at p99.

## Task

Build the complete FastAPI application under `backend/`.

## Phase 1: Project Scaffold

Create:
```
backend/
├── app/
│   ├── main.py
│   ├── config.py            # pydantic-settings, reads env vars
│   ├── dependencies.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── call.py
│   │   ├── billing.py
│   │   └── health.py        # GET /health — required for ECS health check
│   ├── models/
│   │   ├── db/
│   │   │   ├── base.py      # SQLAlchemy Base
│   │   │   ├── account.py
│   │   │   ├── call.py
│   │   │   └── did.py
│   │   └── schemas/
│   │       ├── call.py      # Pydantic request/response models
│   │       └── billing.py
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── credit_service.py
│   │   └── routing_service.py
│   └── core/
│       ├── database.py      # AsyncEngine, AsyncSession
│       ├── redis.py         # aioredis connection pool
│       ├── logging.py       # structlog JSON config
│       └── exceptions.py
├── billing_worker/
│   ├── worker.py
│   └── esl/
│       └── client.py
├── tests/
│   ├── conftest.py          # pytest fixtures: test DB, mock Redis
│   ├── test_call_authorize.py
│   └── test_credit.py
├── Dockerfile
├── docker-compose.yml       # local dev: fastapi + redis + postgres
├── requirements.txt
├── pyproject.toml           # ruff, mypy config
└── alembic/                 # DB migrations
    └── env.py
```

## Phase 2: Core Endpoints

### 2a. POST /v1/call/authorize

This is the most critical endpoint. Called by Lua within 2s of SIP INVITE.

**Request** (from Lua script):
```json
{
  "caller_id": "+94771234567",
  "dialed_number": "+442071234567",
  "inbound_did": "+18001234567",
  "account_token": "token_from_sip_header"
}
```

**Logic:**
1. Lookup account by `account_token` → Redis cache first, then DB
2. Lookup DID mapping for `inbound_did` → DB
3. Check account is active (not suspended, not deleted)
4. Get routing rules for destination prefix of `dialed_number`
5. Check credit balance (Redis `credit:{account_id}`) ≥ minimum (1 min at route rate)
6. If all checks pass: create pending call session in Redis, return success
7. If any check fails: return appropriate error with reason code

**Response (success):**
```json
{
  "success": true,
  "data": {
    "authorized": true,
    "account_id": "uuid",
    "gateway": "gateway_name_for_freeswitch",
    "max_duration_seconds": 3600,
    "rate_per_minute": 0.012,
    "call_uuid": "will-be-set-by-freeswitch",
    "currency": "USD"
  },
  "error": null,
  "request_id": "uuid"
}
```

**Response (insufficient credit):**
```json
{
  "success": false,
  "data": null,
  "error": "INSUFFICIENT_CREDIT",
  "request_id": "uuid"
}
```
→ HTTP 402

### 2b. POST /v1/billing/tick

Called by Lua every 60 seconds during an active call.

**Request:**
```json
{
  "call_uuid": "freeswitch-call-uuid",
  "account_id": "uuid",
  "elapsed_seconds": 60
}
```

**Logic:**
1. Look up call session in Redis: `call:{call_uuid}`
2. Calculate cost for elapsed period: `rate_per_second × elapsed_seconds` (ceiling)
3. Atomically deduct from `credit:{account_id}` using Redis Lua script
4. Update `call:{call_uuid}` last_tick timestamp
5. Return remaining balance as max remaining seconds

**Response:**
```json
{
  "success": true,
  "data": {
    "continue": true,
    "remaining_seconds": 1800,
    "deducted_amount": 0.012
  }
}
```

If `continue: false` → Lua must hangup the call immediately.
If Redis deduction fails → return `continue: false`, log critical alert.

### 2c. GET /health

```json
{
  "status": "healthy",
  "components": {
    "database": "ok",
    "redis": "ok"
  },
  "version": "1.0.0"
}
```

## DB Models to Map

Based on Phase 0 audit, map these legacy tables to SQLAlchemy models.
Use the exact column names from the legacy schema unless they are egregiously bad.
Add `created_at` and `updated_at` (with triggers) if missing.

## Redis Key Schema

Document every Redis key used:
```python
# credit_service.py
CREDIT_KEY = "credit:{account_id}"              # integer, cents × 100
CALL_SESSION_KEY = "call:{call_uuid}"            # hash
CONCURRENT_CALLS_KEY = "concurrent:{account_id}" # integer counter
ACCOUNT_CACHE_KEY = "account:{token}"            # JSON string, TTL 5min
```

## Testing Requirements

Write tests BEFORE considering Phase 2 complete:
- `test_authorize_success` — valid token, sufficient credit
- `test_authorize_insufficient_credit` — balance below threshold
- `test_authorize_invalid_token` — 404 account
- `test_authorize_suspended_account` — 403
- `test_billing_tick_deducts_correctly` — assert Redis DECRBY
- `test_billing_tick_returns_false_on_zero_balance` — continue: false
- `test_billing_tick_atomic` — concurrent ticks don't over-deduct

## Constraints
- No synchronous DB calls in any hot path
- Every DB query must be behind Redis cache for auth/credit
- All monetary values stored as integers (cents × 100), never floats
- Follow coding-standards.md exactly
- Every function needs docstring with telecom context
