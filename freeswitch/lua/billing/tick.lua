--[[
  billing/tick.lua — Periodic credit check during an active call.

  Called via: sched_api "+60 none luarun billing/tick.lua <uuid> <elapsed_seconds>"
  Arguments:  argv[1] = call_uuid, argv[2] = elapsed_seconds (30 for first tick, 60 thereafter)

  This script runs OUTSIDE session context (via luarun, not via session:execute).
  Use freeswitch.api() for all FS operations — no session object available.

  Tick schedule (R-BILL-04: fire BEFORE credit window expires, not after):
    auth.lua fires first tick at +30s with elapsed=30
    tick.lua fires next  tick at +60s with elapsed=60
    Tick times: t=30, t=90, t=150, t=210 ...

  Migration Notes:
    Legacy: coachSessionHandler.pl TIMELOOP — slept to 5s before block expiry,
            then called /check/call/time every ~295s.
            Race condition: if API took >5s, sched_hangup fired first (audit HIGH-02).
    New:    60s fixed interval, no race window. API latency is irrelevant
            because tick.lua kills the call on failure (conservative fail-safe).

  Telecom note:
    On ANY error (API down, session missing), we terminate the call (R-BILL-04).
    It is better to end a call early than to let it run unbilled.
    The billing tick response always returns HTTP 200 from FastAPI — a non-200
    means a transport error and must be treated as fatal.
]]

local config = require("lib.config")
local logger = require("lib.logger")
local http   = require("lib.http")

local call_uuid       = argv[1]
local elapsed_seconds = tonumber(argv[2]) or 60

if not call_uuid or call_uuid == "" then
    logger.error("tick_missing_uuid", { elapsed_seconds = elapsed_seconds })
    return
end

logger.debug("tick_start", {
    call_uuid       = call_uuid,
    elapsed_seconds = elapsed_seconds,
})

-- ── POST /v1/billing/tick ─────────────────────────────────────────────────────
local response, err = http.post(
    config.api_base_url .. "/v1/billing/tick",
    {
        call_uuid       = call_uuid,
        elapsed_seconds = elapsed_seconds,
    },
    config.internal_token,
    config.tick_timeout_ms
)

-- ── Transport error (API unreachable) ─────────────────────────────────────────
-- Conservative: terminate the call. A live call with no billing is worse than
-- a terminated call with a partial CDR.
if err then
    logger.error("tick_api_error", {
        call_uuid = call_uuid,
        error     = err,
    })
    freeswitch.api("uuid_kill", call_uuid)
    return
end

-- ── Credit exhausted or session invalid ───────────────────────────────────────
-- FastAPI returns continue=false when credit runs out or session not found.
-- Play warning, brief pause for caller to hear it, then terminate.
if not response.data or response.data["continue"] == false then
    logger.info("tick_credit_exhausted", {
        call_uuid         = call_uuid,
        remaining_seconds = response.data and response.data.remaining_seconds or 0,
    })

    -- R-FLOW-02: graceful termination — announce before hanging up
    freeswitch.api("uuid_broadcast",
        call_uuid .. " playback::" ..
        config.sounds_base .. "en/creditsnone.mp3 aleg")

    freeswitch.sleep(3000)
    freeswitch.api("uuid_kill", call_uuid)
    return
end

-- ── Credit OK — schedule next tick ────────────────────────────────────────────
local remaining = response.data.remaining_seconds

logger.debug("tick_ok", {
    call_uuid         = call_uuid,
    remaining_seconds = remaining,
    deducted_amount   = response.data.deducted_amount,
})

-- Schedule next tick in 60 seconds with elapsed=60
freeswitch.api("sched_api", "+60 none luarun billing/tick.lua " .. call_uuid .. " 60")
