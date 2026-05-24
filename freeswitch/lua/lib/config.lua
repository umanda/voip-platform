--[[
  lib/config.lua — Runtime configuration for Lua dialplan scripts.

  Reads values from FreeSWITCH global variables (set in conf/vars.xml).
  All keys are prefixed `voip_` to avoid collisions with FS built-ins.

  Migration Notes:
    Legacy: sofia.conf INI file read via getConfValue() (utils.pm)
    New:    FreeSWITCH global vars set from vars.xml or AWS Secrets Manager
            injected at container start via `fs_cli -x "global_setvar key=val"`

  Telecom note:
    Never put secrets (tokens, passwords) in Lua source. They must come
    from the FS global var store, which itself is populated from Secrets Manager.
]]

local M = {}

local function get(key, default)
    local val = freeswitch.getGlobalVariable(key)
    if val and val ~= "" then
        return val
    end
    return default
end

-- FastAPI endpoint — must be reachable from FreeSWITCH EC2 within 2000ms (R-SIP-01)
M.api_base_url     = get("voip_api_base_url",     "http://127.0.0.1:8000")

-- Internal service token — set from AWS Secrets Manager at startup
M.internal_token   = get("voip_internal_token",   "")

-- Sound file base paths — language subdirectory appended at runtime (e.g. "en/")
M.sounds_base      = get("voip_sounds_base",      "/usr/share/freeswitch/sounds/galaxy/")
M.digits_base      = get("voip_digits_base",      "/usr/share/freeswitch/sounds/galaxy/en/digit/")

-- IVR tuning — matches legacy sofia.conf [auth] section
M.auth_attempts    = tonumber(get("voip_auth_attempts",   "3"))
M.pin_length       = tonumber(get("voip_pin_length",      "8"))
M.dtmf_timeout_ms  = tonumber(get("voip_dtmf_timeout_ms", "5000"))

-- HTTP timeout for API calls in call path (R-SIP-01: ≤ 2000ms)
M.http_timeout_ms  = tonumber(get("voip_http_timeout_ms", "2000"))
-- Slightly shorter timeout for billing tick — less critical than auth
M.tick_timeout_ms  = tonumber(get("voip_tick_timeout_ms", "1500"))

-- Credit warning thresholds — from legacy sofia.conf [dial_coach]
M.warn_remaining_seconds  = tonumber(get("voip_warn_remaining_seconds",  "320"))
M.beep_remaining_seconds  = tonumber(get("voip_beep_remaining_seconds",  "10"))

-- Gateway fallback — matches legacy sofia.conf [providers]
M.default_gateway  = get("voip_default_gateway",  "voxbone-outbound")

return M
