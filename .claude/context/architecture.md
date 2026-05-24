# Architecture Context — VoIP Platform Modernization

## System Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                          AWS Region                              │
│                                                                 │
│  ┌──────────────┐     ┌─────────────────────────────────────┐   │
│  │   Route 53   │     │           VPC                        │   │
│  │  (DNS/SIP)   │     │                                      │   │
│  └──────┬───────┘     │  ┌──────────────┐  ┌─────────────┐  │   │
│         │             │  │   Public     │  │   Private   │  │   │
│  ┌──────▼───────┐     │  │   Subnet     │  │   Subnet    │  │   │
│  │   Voxbone    │     │  │              │  │             │  │   │
│  │  (SIP trunk) │     │  │ ┌──────────┐ │  │ ┌─────────┐ │  │   │
│  └──────┬───────┘     │  │ │FreeSWITCH│ │  │ │FastAPI  │ │  │   │
│         │ SIP/RTP     │  │ │  EC2     │ │  │ │ ECS     │ │  │   │
│         └─────────────┼──┼─►(t3.xlarge)│ │  │ Fargate │ │  │   │
│                        │  │ │          ├─┼──┼─►         │ │  │   │
│                        │  │ │  Lua     │ │  │ │         │ │  │   │
│                        │  │ │  scripts │ │  │ └────┬────┘ │  │   │
│                        │  │ └──────────┘ │  │      │      │  │   │
│                        │  │              │  │ ┌────▼────┐ │  │   │
│                        │  │              │  │ │Billing  │ │  │   │
│                        │  │              │  │ │Worker   │ │  │   │
│                        │  │              │  │ │ ECS     │ │  │   │
│                        │  └──────────────┘  │ └────┬────┘ │  │   │
│                        │                    │      │      │  │   │
│                        │                    │ ┌────▼────┐ │  │   │
│                        │                    │ │ElastiC- │ │  │   │
│                        │                    │ │ache     │ │  │   │
│                        │                    │ │ Redis   │ │  │   │
│                        │                    │ └────┬────┘ │  │   │
│                        │                    │      │      │  │   │
│                        │                    │ ┌────▼────┐ │  │   │
│                        │                    │ │   RDS   │ │  │   │
│                        │                    │ │Postgres │ │  │   │
│                        │                    │ │(later)  │ │  │   │
│                        │                    └──────────────┘  │   │
│                        └─────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

### FreeSWITCH (EC2 — t3.xlarge minimum)
- SIP signaling (UDP 5060, TCP 5060, TLS 5061)
- RTP media (UDP 16384–32768)
- ESL (Event Socket Layer) on 8021
- Lua script execution (dialplan + billing hooks)
- **Must be EC2** — Fargate cannot handle RTP/UDP at scale
- Elastic IP required for SIP trunk registration with Voxbone

### Lua Scripts (on FreeSWITCH EC2)
- `dialplan/auth.lua` — HTTP call to FastAPI /v1/call/authorize
- `dialplan/route.lua` — sets outbound gateway from API response
- `billing/tick.lua` — periodic credit check during call
- `billing/hangup.lua` — fires hangup event to billing worker
- `lib/http.lua` — shared HTTP client (using luasocket or freeswitch.api)
- `lib/logger.lua` — structured JSON logging

### FastAPI (ECS Fargate)
- REST API consumed by Lua scripts and admin UIs
- Routers: `/auth`, `/call`, `/billing`, `/routing`, `/admin`
- JWT-based auth for internal service calls (Lua → FastAPI)
- Pydantic v2 models for all request/response schemas
- SQLAlchemy async for DB access
- Redis for credit cache

### Billing Worker (ECS Fargate)
- Connects to FreeSWITCH ESL (port 8021)
- Subscribes to: CHANNEL_CREATE, CHANNEL_ANSWER, CHANNEL_HANGUP_COMPLETE, CHANNEL_BRIDGE
- On HANGUP: writes final CDR to PostgreSQL, reconciles Redis balance
- Handles crash recovery: on startup, reconcile any open Redis call sessions with DB

### Redis (ElastiCache)
- Key: `credit:{account_id}` → integer (balance in credit units, e.g. seconds or cents × 100)
- Key: `call:{call_uuid}` → hash {start_time, account_id, rate, gateway}
- Key: `rate_limit:{account_id}` → sorted set for concurrent call limiting
- Credit deduction: atomic Lua script (DECRBY) — never application-level read-modify-write

### PostgreSQL
- Currently EC2-hosted (galaxy_2 DB)
- Migration target: RDS PostgreSQL (Phase 6+)
- Schema must be mapped from existing Helios/Laravel schema
- CDR table must support append-only pattern (never UPDATE a finalized CDR)

## Key API Contracts

### POST /v1/call/authorize
Request (from Lua):
```json
{
  "caller_id": "+94771234567",
  "dialed_number": "+442071234567",
  "inbound_did": "+18001234567",
  "account_token": "xxx"
}
```
Response:
```json
{
  "success": true,
  "data": {
    "authorized": true,
    "account_id": "uuid",
    "gateway": "gateway_name",
    "max_duration_seconds": 3600,
    "rate_per_minute": 0.012,
    "call_uuid": "freeswitch-uuid"
  }
}
```

### POST /v1/billing/tick
Request (from Lua, every 60s):
```json
{
  "call_uuid": "xxx",
  "elapsed_seconds": 60,
  "account_id": "uuid"
}
```
Response:
```json
{
  "success": true,
  "data": {
    "continue": true,
    "remaining_seconds": 1800
  }
}
```
If `continue: false` → Lua must hangup the call immediately.

## Networking Requirements

| Port      | Protocol | Direction        | Purpose                    |
|-----------|----------|------------------|----------------------------|
| 5060      | UDP/TCP  | Inbound          | SIP signaling (Voxbone)    |
| 5061      | TLS      | Inbound          | SIP TLS                    |
| 16384-32768 | UDP    | Inbound+Outbound | RTP media                  |
| 8021      | TCP      | Internal         | FreeSWITCH ESL             |
| 8000      | TCP      | Internal         | FastAPI                    |
| 6379      | TCP      | Internal         | Redis                      |
| 5432      | TCP      | Internal         | PostgreSQL                 |

## Security Architecture

- FreeSWITCH ESL: restricted to VPC internal only (SG rule)
- FastAPI: ALB → ECS, no direct public access to ECS tasks
- SIP: Voxbone IP whitelist on FreeSWITCH SG
- DB: private subnet, no public access
- All secrets: AWS Secrets Manager, fetched at container start
- Service-to-service auth: internal JWT with short TTL (15min)
