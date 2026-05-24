--[[
  dialplan/auth.lua — Authorize an inbound call before FreeSWITCH bridges it.

  Entry point: called from dialplan as <action application="lua" data="dialplan/auth.lua"/>

  Call flow:
    1. Set channel flags (continue_on_fail, ignore_early_media)
    2. Answer and extract call metadata
    3. If caller has pre-auth SIP header (X-Auth-Token) → skip DTMF
    4. Otherwise collect 8-digit PIN via DTMF (up to 3 attempts)
    5. POST /v1/call/authorize — 2000ms hard timeout (R-SIP-01)
    6. On success: set voip_gateway + voip_destination_number, schedule billing tick
    7. On any failure: play appropriate IVR sound, hangup with cause

  Migration Notes:
    Legacy: checkServiceType.pl (DID routing) + authentication.pl (PIN collection)
            + dialCoach.pl (bridge setup) — three Perl scripts with no timeout.
    New:    Single Lua script, one API call, 2000ms timeout (R-FLOW-01).
            DTMF collection loop replaces authentication.pl.
            Parallel perlrun processes replaced by sched_api (R-BILL-04).

  Telecom notes:
    - NEVER log the PIN value — only log account_id after auth succeeds
    - All phone numbers normalized (strip '+') before API call
    - On timeout: route to error IVR, not silent drop (R-FLOW-01)
    - First billing tick at +30s, not +60s (R-BILL-04: fire before block expires)
]]

local config = require("lib.config")
local logger = require("lib.logger")
local http   = require("lib.http")
local utils  = require("lib.utils")

-- ── Channel flags (match legacy checkServiceType.pl setVariable calls) ────────
session:setVariable("hangup_after_bridge",  "false")
session:setVariable("continue_on_fail",     "true")
session:setVariable("ignore_early_media",   "true")

-- ── Extract call metadata ─────────────────────────────────────────────────────
local call_uuid     = session:getVariable("uuid")
local caller_id     = utils.normalize_e164(session:getVariable("caller_id_number"))
local dialed_number = utils.normalize_e164(session:getVariable("destination_number"))
local inbound_did   = utils.normalize_e164(session:getVariable("sip_to_user") or
                          session:getVariable("destination_number"))

logger.info("auth_start", {
    call_uuid   = call_uuid,
    caller_id   = caller_id,
    inbound_did = inbound_did,
})

-- ── Answer the call (required before DTMF collection) ────────────────────────
session:answer()
session:sleep(500)  -- brief pause matches legacy session->sleep(500ms)

-- ── Resolve sound path based on language ─────────────────────────────────────
-- language_id is not yet known until /authorize returns, so default to English.
-- Legacy sets language after /call/validate; we use English for PIN prompt.
local sounds = config.sounds_base .. "en/"

-- ── Collect PIN — DTMF or SIP header pre-auth ─────────────────────────────────
-- Pre-auth callers (ivr_known_users or SIP header) skip DTMF entirely.
local account_token = session:getVariable("sip_h_X-Auth-Token")
local direct_auth   = (account_token and account_token ~= "")

if not direct_auth then
    -- DTMF collection loop — up to config.auth_attempts attempts
    -- Matches legacy authentication.pl collect-pin ATTEMPT loop
    local attempts = 0
    while attempts < config.auth_attempts do
        attempts = attempts + 1

        -- play_and_get_digits: min max tries timeout terminator prompt bad_input var regexp
        -- tries=1 here so we control retry messaging ourselves
        session:execute("play_and_get_digits",
            config.pin_length .. " " ..
            config.pin_length .. " 1 " ..
            config.dtmf_timeout_ms .. " # " ..
            sounds .. "creditsmenu.mp3 " ..
            sounds .. "empty_pin.mp3 " ..
            "voip_pin \\d{" .. config.pin_length .. "}")

        local entered = session:getVariable("voip_pin")

        if utils.is_numeric(entered) and #entered == config.pin_length then
            account_token = entered
            break
        end

        -- Wrong or empty — play error and retry (except on last attempt)
        account_token = nil
        if attempts < config.auth_attempts then
            session:execute("playback", sounds .. "creditcodewrong.mp3")
            session:sleep(500)
        end
    end

    if not account_token then
        logger.warn("auth_pin_max_attempts", {
            call_uuid = call_uuid,
            caller_id = caller_id,
            attempts  = config.auth_attempts,
        })
        session:execute("playback", sounds .. "login_failed.mp3")
        session:hangup("NORMAL_CLEARING")
        return
    end
end

-- ── POST /v1/call/authorize (2000ms hard timeout — R-SIP-01) ─────────────────
local response, err = http.post(
    config.api_base_url .. "/v1/call/authorize",
    {
        call_uuid     = call_uuid,
        caller_id     = caller_id,
        inbound_did   = inbound_did,
        dialed_number = dialed_number,
        account_token = account_token,
    },
    config.internal_token,
    config.http_timeout_ms
)

-- ── Handle API errors (timeout, connection failure) ───────────────────────────
-- R-FLOW-01: never drop silently — always route to error IVR
if err then
    logger.error("auth_api_error", {
        call_uuid = call_uuid,
        caller_id = caller_id,
        error     = err,
    })
    session:execute("playback", sounds .. "something_wrong.mp3")
    session:hangup("SERVICE_UNAVAILABLE")
    return
end

-- ── Handle auth denial ────────────────────────────────────────────────────────
if not response or not response.success then
    local reason = (response and response.error) or "UNKNOWN"

    logger.warn("auth_denied", {
        call_uuid = call_uuid,
        caller_id = caller_id,
        reason    = reason,
    })

    if reason == "INSUFFICIENT_CREDIT" then
        session:execute("playback", sounds .. "creditsnone.mp3")
        session:hangup("NORMAL_CLEARING")

    elseif reason == "ACCOUNT_NOT_FOUND" then
        session:execute("playback", sounds .. "creditcodewrong.mp3")
        session:sleep(500)
        session:execute("playback", sounds .. "login_failed.mp3")
        session:hangup("CALL_REJECTED")

    elseif reason == "ACCOUNT_SUSPENDED" then
        session:execute("playback", sounds .. "login_failed.mp3")
        session:hangup("CALL_REJECTED")

    elseif reason == "DID_NOT_FOUND" then
        session:execute("playback", sounds .. "something_wrong.mp3")
        session:hangup("UNALLOCATED_NUMBER")

    else
        session:execute("playback", sounds .. "something_wrong.mp3")
        session:hangup("SERVICE_UNAVAILABLE")
    end
    return
end

-- ── Auth success ──────────────────────────────────────────────────────────────
local data = response.data

logger.info("auth_success", {
    call_uuid      = call_uuid,
    account_id     = data.account_id,
    gateway        = data.gateway,
    service_type   = data.service_type,
    max_duration_s = data.max_duration_seconds,
    direct_auth    = direct_auth,
})

session:execute("playback", sounds .. "login_success.mp3")

-- Set channel variables consumed by the bridge action in dialplan XML
session:setVariable("voip_account_id",        tostring(data.account_id))
session:setVariable("voip_gateway",            data.gateway)
session:setVariable("voip_destination_number", data.destination_number)
session:setVariable("voip_rate_per_minute",    tostring(data.rate_per_minute))
session:setVariable("voip_max_duration",       tostring(data.max_duration_seconds))
session:setVariable("voip_service_type",       tostring(data.service_type))

-- Hard cap on call duration — FreeSWITCH auto-hangs up (matches legacy sched_hangup)
session:execute("sched_hangup",
    "+" .. tostring(data.max_duration_seconds) .. " allotted_timeout")

-- Credit low warning at (max_duration - warn_threshold) seconds
-- Matches legacy coachSessionHandler.pl creditslow check
local warn_at = data.max_duration_seconds - config.warn_remaining_seconds
if warn_at > 5 then
    session:execute("sched_api",
        "+" .. tostring(warn_at) .. " none uuid_broadcast " ..
        call_uuid .. " playback::" .. sounds .. "creditslow_8000.mp3 aleg")
end

-- Countdown beep at (max_duration - beep_threshold) seconds
local beep_at = data.max_duration_seconds - config.beep_remaining_seconds
if beep_at > 5 then
    session:execute("sched_api",
        "+" .. tostring(beep_at) .. " none uuid_broadcast " ..
        call_uuid .. " playback::" .. sounds .. "count-down-beep.mp3 aleg")
end

-- First billing tick at +30s — R-BILL-04: fire BEFORE block expires, not after.
-- Subsequent ticks are scheduled by tick.lua itself (+60s each).
session:execute("sched_api",
    "+30 none luarun billing/tick.lua " .. call_uuid .. " 30")

logger.debug("auth_scheduled", {
    call_uuid = call_uuid,
    warn_at_s = warn_at,
    beep_at_s = beep_at,
    tick_at_s = 30,
})
