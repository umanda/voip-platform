# Prompt: Migrate Perl to Lua (Phase 3)

## Prerequisites
- Phase 0 complete: `docs/legacy-audit/sofia-analysis.md` exists
- Phase 2 complete: FastAPI `/v1/call/authorize` and `/v1/billing/tick` are working
- You have read `.claude/context/telecom-rules.md` — especially R-SIP-*, R-BILL-*, R-FLOW-*
- You have read `.claude/context/coding-standards.md` — the Lua section

## Your Role
You are rewriting the FreeSWITCH Perl dialplan scripts in Lua.
Lua is native to FreeSWITCH, has lower latency, and eliminates process spawning.
You MUST preserve every behavior documented in `docs/legacy-audit/sofia-analysis.md`.

## Task

Build all Lua scripts under `freeswitch/lua/`.

## Project Structure

```
freeswitch/lua/
├── dialplan/
│   ├── auth.lua          # Main entry: called on SIP INVITE
│   └── route.lua         # Sets outbound gateway after auth
├── billing/
│   ├── tick.lua          # Called every 60s during call (via schedule_hangup pattern)
│   └── hangup.lua        # Called on CHANNEL_HANGUP_COMPLETE
├── lib/
│   ├── http.lua          # HTTP client with timeout
│   ├── logger.lua        # Structured JSON logger
│   ├── config.lua        # Read config from FS vars or env
│   └── utils.lua         # E.164 normalize, phone number utils
└── tests/
    ├── test_auth.lua
    └── test_billing.lua
```

## Script 1: `dialplan/auth.lua`

This runs when FreeSWITCH receives a SIP INVITE.
It MUST complete within 2000ms total.

```lua
-- auth.lua
-- Called from FreeSWITCH dialplan: <action application="lua" data="auth.lua"/>
-- Purpose: Authorize call with FastAPI before bridging

local logger = require("lib.logger")
local http = require("lib.http")
local config = require("lib.config")
local utils = require("lib.utils")

-- Get call variables from FreeSWITCH session
local call_uuid = session:getVariable("uuid")
local caller_id = utils.normalize_e164(session:getVariable("caller_id_number"))
local dialed_number = utils.normalize_e164(session:getVariable("destination_number"))
local inbound_did = utils.normalize_e164(session:getVariable("sip_to_user"))
local account_token = session:getVariable("sip_h_X-Auth-Token")

logger.info("auth_request", {
    call_uuid = call_uuid,
    caller_id = caller_id,
    dialed_number = dialed_number,
})

-- Call FastAPI authorize endpoint
local response, err = http.post(
    config.api_base_url .. "/v1/call/authorize",
    {
        caller_id = caller_id,
        dialed_number = dialed_number,
        inbound_did = inbound_did,
        account_token = account_token,
    },
    config.internal_token,
    2000  -- 2000ms hard timeout
)

if err or not response then
    -- API unreachable — route to error IVR, never drop silently
    logger.error("auth_api_timeout", { call_uuid = call_uuid, error = err })
    session:execute("playback", "ivr/auth_unavailable.wav")
    session:hangup("SERVICE_UNAVAILABLE")
    return
end

if not response.success or response.data.authorized ~= true then
    local reason = response.error or "UNKNOWN"
    logger.warn("auth_denied", { call_uuid = call_uuid, reason = reason })
    
    if reason == "INSUFFICIENT_CREDIT" then
        session:execute("playback", "ivr/insufficient_credit.wav")
        session:hangup("NORMAL_CLEARING")
    else
        session:execute("playback", "ivr/auth_failed.wav")
        session:hangup("CALL_REJECTED")
    end
    return
end

-- Auth success — set call variables for route.lua
local data = response.data
session:setVariable("voip_account_id", data.account_id)
session:setVariable("voip_gateway", data.gateway)
session:setVariable("voip_max_duration", tostring(data.max_duration_seconds))
session:setVariable("voip_rate_per_minute", tostring(data.rate_per_minute))

-- Set max call duration (FreeSWITCH will hangup automatically)
session:execute("sched_hangup", "+" .. data.max_duration_seconds .. " allotted_timeout")

logger.info("auth_success", {
    call_uuid = call_uuid,
    account_id = data.account_id,
    gateway = data.gateway,
    max_duration = data.max_duration_seconds,
})
```

## Script 2: FreeSWITCH Dialplan XML

The Lua scripts are invoked from dialplan XML.
Create `freeswitch/conf/dialplan/default.xml` with:

```xml
<extension name="voip_inbound">
  <condition field="destination_number" expression="^(\+?[0-9]+)$">
    <!-- Step 1: Authorize -->
    <action application="lua" data="dialplan/auth.lua"/>
    
    <!-- Step 2: Only bridge if auth set the gateway variable -->
    <action application="bridge" 
            data="${sofia/gateway/${voip_gateway}/${destination_number}}"/>
    
    <!-- Step 3: Post-call -->
    <action application="lua" data="billing/hangup.lua"/>
  </condition>
</extension>
```

## Script 3: `billing/tick.lua`

Called every N seconds to check if credit is still available.
Use FreeSWITCH `sched_api` to schedule periodic ticks.

```lua
-- tick.lua
-- Scheduled via: session:execute("sched_api", "+60 none luarun billing/tick.lua <uuid>")
-- Called with call UUID as argument

local call_uuid = argv[1]
local logger = require("lib.logger")
local http = require("lib.http")
local config = require("lib.config")

if not call_uuid then
    logger.error("tick_missing_uuid", {})
    return
end

local response, err = http.post(
    config.api_base_url .. "/v1/billing/tick",
    {
        call_uuid = call_uuid,
        elapsed_seconds = 60,
    },
    config.internal_token,
    1500
)

if err or not response or not response.success then
    -- Billing API down — conservative: hangup to prevent unbilled calls
    logger.error("tick_api_error", { call_uuid = call_uuid, error = err })
    freeswitch.api("uuid_kill", call_uuid)
    return
end

if not response.data.continue then
    logger.info("tick_credit_exhausted", { call_uuid = call_uuid })
    -- Play warning then hangup
    freeswitch.api("uuid_broadcast", call_uuid .. " playback::ivr/credit_exhausted.wav aleg")
    freeswitch.sleep(3000)
    freeswitch.api("uuid_kill", call_uuid)
    return
end

-- Schedule next tick
freeswitch.api("sched_api", "+60 none luarun billing/tick.lua " .. call_uuid)
logger.debug("tick_ok", {
    call_uuid = call_uuid,
    remaining_seconds = response.data.remaining_seconds,
})
```

## Script 4: `lib/http.lua`

```lua
-- lib/http.lua
-- HTTP client with mandatory timeout
-- Uses luasocket (must be installed on FreeSWITCH EC2)

local M = {}
local http = require("socket.http")
local ltn12 = require("ltn12")
local json = require("cjson")

function M.post(url, body, token, timeout_ms)
    http.TIMEOUT = (timeout_ms or 2000) / 1000
    
    local body_str = json.encode(body)
    local response_body = {}
    
    local ok, code, headers = http.request({
        url = url,
        method = "POST",
        headers = {
            ["Content-Type"] = "application/json",
            ["Content-Length"] = tostring(#body_str),
            ["Authorization"] = "Bearer " .. token,
        },
        source = ltn12.source.string(body_str),
        sink = ltn12.sink.table(response_body),
    })
    
    if not ok then
        return nil, "http_error:" .. tostring(code)
    end
    
    if code ~= 200 and code ~= 402 and code ~= 403 then
        return nil, "unexpected_status:" .. tostring(code)
    end
    
    local ok_parse, parsed = pcall(json.decode, table.concat(response_body))
    if not ok_parse then
        return nil, "json_parse_error"
    end
    
    return parsed, nil
end

return M
```

## FreeSWITCH Config Files Required

Create all necessary conf files:
- `freeswitch/conf/autoload_configs/lua.conf.xml` — Lua module config, startup script
- `freeswitch/conf/sip_profiles/internal.xml` — internal SIP profile
- `freeswitch/conf/sip_profiles/external.xml` — Voxbone trunk profile
- `freeswitch/conf/directory/default.xml` — user directory
- `freeswitch/conf/vars.xml` — global variables (API URL, token, etc.)

## Deployment Notes

Lua scripts are hot-reloadable:
```bash
fs_cli -x "reload mod_lua"
```

After any Lua change, run this — NO FreeSWITCH restart needed.
After XML dialplan changes:
```bash
fs_cli -x "reloadxml"
```

## Testing Approach

Since Lua runs inside FreeSWITCH, test using:
1. `docker-compose` with FreeSWITCH + mock FastAPI
2. Send test SIP calls using `sipp` or `baresip`
3. Verify auth is called (check FastAPI logs)
4. Verify billing ticks fire at correct intervals
5. Verify hangup fires when `continue: false` returned

## Constraints
- NEVER use blocking calls > 2s in dialplan context
- NEVER print sensitive data (account tokens, passwords) to FS log
- ALL phone numbers must be normalized to E.164 before any API call
- Lua scripts must be idempotent (safe to run twice with same UUID)
- Test with: busy destination, no-answer, rejected, mid-call hangup
