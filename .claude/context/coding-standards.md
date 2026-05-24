# Coding Standards

## Python (FastAPI + Billing Worker)

### Style
- Python 3.11+
- PEP 8, enforced by `ruff`
- Type hints on every function signature — no exceptions
- Pydantic v2 for all request/response models
- `async`/`await` throughout — no synchronous DB or HTTP calls in request handlers

### Project Structure (FastAPI)
```
backend/
├── app/
│   ├── main.py              # FastAPI app factory
│   ├── config.py            # Settings (pydantic-settings, reads from env/Secrets Manager)
│   ├── dependencies.py      # FastAPI dependencies (get_db, get_redis, get_current_account)
│   ├── routers/
│   │   ├── call.py          # /v1/call/authorize, /v1/call/hangup
│   │   ├── billing.py       # /v1/billing/tick, /v1/billing/finalize
│   │   ├── auth.py          # /v1/auth/token (internal service auth)
│   │   ├── routing.py       # /v1/routing/lookup
│   │   └── admin.py         # /v1/admin/* (account mgmt)
│   ├── models/
│   │   ├── db/              # SQLAlchemy ORM models
│   │   └── schemas/         # Pydantic request/response schemas
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── billing_service.py
│   │   ├── routing_service.py
│   │   └── credit_service.py
│   └── core/
│       ├── database.py      # Async SQLAlchemy engine
│       ├── redis.py         # Redis connection pool
│       ├── logging.py       # Structured JSON logging
│       └── exceptions.py    # Custom exception classes
├── billing_worker/
│   ├── worker.py            # ESL event loop
│   ├── handlers/
│   │   ├── call_answer.py
│   │   ├── call_hangup.py
│   │   └── reconcile.py
│   └── esl/
│       └── client.py        # FreeSWITCH ESL client wrapper
└── tests/
    ├── test_billing.py
    ├── test_auth.py
    ├── test_routing.py
    └── conftest.py
```

### Response Envelope (ALL API responses)
```python
class APIResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    request_id: str  # UUID, for log correlation

# Always return this — never raw data at root level
```

### Error Handling
```python
# Define domain exceptions
class InsufficientCreditError(Exception):
    pass

class AccountNotFoundError(Exception):
    pass

class CallAuthorizationError(Exception):
    pass

# Use FastAPI exception handlers — never return 200 with error in body
@app.exception_handler(InsufficientCreditError)
async def insufficient_credit_handler(request, exc):
    return JSONResponse(status_code=402, content={...})
```

### Database Access
- Use SQLAlchemy 2.0 async with `AsyncSession`
- Never use raw SQL strings (use ORM or `text()` with bound params)
- Connection pool: `pool_size=10, max_overflow=20`
- Always use `async with session.begin()` for write transactions

### Configuration
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    freeswitch_esl_host: str
    freeswitch_esl_port: int = 8021
    freeswitch_esl_password: str
    internal_jwt_secret: str
    
    class Config:
        env_file = ".env"  # local dev only
        # Production: values injected from AWS Secrets Manager at container start
```

### Docstring Format
```python
async def authorize_call(
    caller_id: str,
    dialed_number: str,
    account_token: str,
) -> CallAuthorizationResult:
    """
    Authorize an inbound call before FreeSWITCH bridges it.
    
    Called by Lua dialplan immediately after SIP INVITE is received.
    Must respond within 2000ms or FreeSWITCH will timeout.
    
    Args:
        caller_id: E.164 format caller number (from SIP From header)
        dialed_number: E.164 format dialed number (Voxbone DID)
        account_token: Account authentication token (from SIP X-Auth header)
    
    Returns:
        CallAuthorizationResult with gateway, max_duration, rate
    
    Raises:
        AccountNotFoundError: Token doesn't match any account
        InsufficientCreditError: Balance below minimum threshold
        RoutingError: No gateway available for destination
    
    Telecom note:
        This function is in the critical path of call setup.
        Any DB query here must be backed by Redis cache.
    """
```

---

## Lua (FreeSWITCH Dialplan)

### Style
- Lua 5.1 (FreeSWITCH built-in version)
- No global variables — use module pattern
- All HTTP calls via `luasocket` or `freeswitch.api("curl ...")`
- Structured logging to FreeSWITCH log (JSON format)

### Project Structure (Lua)
```
freeswitch/lua/
├── dialplan/
│   ├── auth.lua          # Entry point: authorize call
│   └── route.lua         # Set outbound gateway
├── billing/
│   ├── tick.lua          # Periodic credit check
│   └── hangup.lua        # Hangup event handler
└── lib/
    ├── http.lua          # HTTP client wrapper
    ├── logger.lua        # Structured JSON logger
    ├── config.lua        # Config (reads from env or FS vars)
    └── utils.lua         # E.164 normalization, etc.
```

### Logging Pattern
```lua
-- lib/logger.lua
local M = {}
local json = require("json")  -- or use cjson

function M.log(level, event_type, data)
    data.timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ")
    data.component = "lua"
    data.event_type = event_type
    freeswitch.log(level, json.encode(data))
end

return M
```

### HTTP Call Pattern
```lua
-- Always set timeout, always handle errors
local function call_api(endpoint, payload)
    local http = require("socket.http")
    local ltn12 = require("ltn12")
    
    http.TIMEOUT = 2  -- 2 second hard limit
    
    local response_body = {}
    local ok, code = http.request({
        url = config.api_base_url .. endpoint,
        method = "POST",
        headers = {
            ["Content-Type"] = "application/json",
            ["Authorization"] = "Bearer " .. config.internal_token,
        },
        source = ltn12.source.string(json.encode(payload)),
        sink = ltn12.sink.table(response_body),
    })
    
    if not ok or code ~= 200 then
        logger.log("ERR", "api_call_failed", {
            endpoint = endpoint,
            http_code = code,
            call_uuid = session:getVariable("uuid"),
        })
        return nil, "api_error"
    end
    
    return json.decode(table.concat(response_body)), nil
end
```

---

## AWS CDK (Python)

### Structure
```
infrastructure/
├── app.py                    # CDK app entry point
├── cdk.json
├── stacks/
│   ├── network_stack.py      # VPC, subnets, SGs
│   ├── freeswitch_stack.py   # EC2, EIP, user-data
│   ├── api_stack.py          # ECS Fargate, ALB, ECR
│   ├── data_stack.py         # ElastiCache, RDS (future)
│   ├── secrets_stack.py      # Secrets Manager
│   └── monitoring_stack.py   # CloudWatch dashboards, alarms
└── constructs/
    ├── freeswitch_instance.py
    └── fargate_service.py
```

### CDK Rules
- Use `RemovalPolicy.RETAIN` for all stateful resources (RDS, ElastiCache)
- Tag every resource: `Project=voip-platform, Environment=prod/staging`
- Use `aws_cdk.aws_ssm` for cross-stack references (not hardcoded ARNs)
- All secrets: `aws_secretsmanager.Secret`, never `CfnParameter` for credentials

---

## Git Conventions

```
feat(billing): add Redis atomic credit deduction
fix(lua): handle HTTP timeout in auth.lua
chore(infra): add RDS security group rule
docs(api): update call authorize endpoint schema
test(billing): add reconciliation worker tests
```

## Testing Requirements

| Component      | Required Tests                              |
|----------------|---------------------------------------------|
| Credit service | Atomic deduction, insufficient funds, crash recovery |
| Auth service   | Valid token, expired, not found, rate limit |
| Routing        | Prefix match, fallback, no route available  |
| Billing worker | CHANNEL_HANGUP handling, CDR write, reconcile |
| Lua scripts    | Mock API responses, timeout behavior         |
