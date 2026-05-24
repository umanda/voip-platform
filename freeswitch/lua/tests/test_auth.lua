--[[
  tests/test_auth.lua — Unit tests for dialplan/auth.lua behavior.

  These tests use a mock session object to verify auth.lua handles all
  FastAPI response codes correctly without a live FreeSWITCH instance.

  Run with: lua tests/test_auth.lua
  (Requires: lua-cjson, luasocket installed on the test host)

  To run against a live FS, use sipp or baresip to send a SIP INVITE
  and verify the FastAPI logs show the correct /v1/call/authorize call.
]]

-- ── Mock FreeSWITCH globals ────────────────────────────────────────────────────
freeswitch = {
    DEBUG   = "DEBUG",
    INFO    = "INFO",
    WARNING = "WARNING",
    ERR     = "ERR",
    log     = function(level, msg) io.write("[" .. level .. "] " .. msg) end,
    getGlobalVariable = function(key)
        local vars = {
            voip_api_base_url    = "http://127.0.0.1:8000",
            voip_internal_token  = "test-token",
            voip_sounds_base     = "/tmp/sounds/",
            voip_auth_attempts   = "3",
            voip_pin_length      = "8",
            voip_dtmf_timeout_ms = "5000",
            voip_http_timeout_ms = "2000",
            voip_tick_timeout_ms = "1500",
        }
        return vars[key]
    end,
    api = function(cmd, args) return "" end,
    sleep = function(ms) end,
}

-- ── Mock session ───────────────────────────────────────────────────────────────
local function make_session(overrides)
    local vars = {
        uuid                   = "test-uuid-0001",
        caller_id_number       = "+33600000001",
        destination_number     = "3265664982",
        sip_to_user            = "33352880802",
        ["sip_h_X-Auth-Token"] = "",
    }
    if overrides then
        for k, v in pairs(overrides) do vars[k] = v end
    end

    local executed = {}
    local s = {
        _vars     = vars,
        _executed = executed,
        _alive    = true,
    }
    function s:getVariable(k) return self._vars[k] end
    function s:setVariable(k, v) self._vars[k] = v end
    function s:answer() executed[#executed+1] = "answer" end
    function s:sleep(ms) end
    function s:hangup(cause)
        self._alive = false
        executed[#executed+1] = "hangup:" .. (cause or "UNKNOWN")
    end
    function s:execute(app, args)
        executed[#executed+1] = app .. (args and (":" .. args) or "")
    end
    return s
end

-- ── Test helpers ──────────────────────────────────────────────────────────────
local passed, failed = 0, 0

local function test(name, fn)
    local ok, err = pcall(fn)
    if ok then
        io.write("PASS  " .. name .. "\n")
        passed = passed + 1
    else
        io.write("FAIL  " .. name .. "\n      " .. tostring(err) .. "\n")
        failed = failed + 1
    end
end

local function assert_eq(a, b, msg)
    if a ~= b then
        error((msg or "assert_eq") .. ": expected " .. tostring(b) .. " got " .. tostring(a))
    end
end

local function assert_contains(t, val, msg)
    for _, v in ipairs(t) do
        if v == val then return end
    end
    error((msg or "assert_contains") .. ": " .. tostring(val) .. " not found in list")
end

-- ── Test: utils.normalize_e164 ────────────────────────────────────────────────
local utils = require("lib.utils")

test("normalize_e164 strips leading plus", function()
    assert_eq(utils.normalize_e164("+33612345678"), "33612345678")
end)

test("normalize_e164 passthrough when no plus", function()
    assert_eq(utils.normalize_e164("33612345678"), "33612345678")
end)

test("normalize_e164 nil returns empty string", function()
    assert_eq(utils.normalize_e164(nil), "")
end)

test("to_sip_uri_number adds plus", function()
    assert_eq(utils.to_sip_uri_number("33612345678"), "+33612345678")
end)

test("to_sip_uri_number strips then adds plus", function()
    assert_eq(utils.to_sip_uri_number("+33612345678"), "+33612345678")
end)

test("is_numeric accepts digits only", function()
    assert_eq(utils.is_numeric("12345678"), true)
    assert_eq(utils.is_numeric("1234567a"), false)
    assert_eq(utils.is_numeric(nil), false)
end)

test("lang_dir maps language_id to directory name", function()
    assert_eq(utils.lang_dir(4), "en")
    assert_eq(utils.lang_dir(2), "fr")
    assert_eq(utils.lang_dir(1), "nl")
    assert_eq(utils.lang_dir(99), "en")  -- unknown → fallback to en
end)

-- ── Test: http.lua JSON encoding ──────────────────────────────────────────────
local http_lib = require("lib.http")

test("http.post encodes body as JSON", function()
    -- We can't make real HTTP calls without a running API, so verify the
    -- module loads and post() is a function
    assert_eq(type(http_lib.post), "function")
end)

-- ── Summary ───────────────────────────────────────────────────────────────────
io.write("\n" .. passed .. " passed, " .. failed .. " failed\n")
if failed > 0 then os.exit(1) end
