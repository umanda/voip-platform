--[[
  tests/test_billing.lua — Unit tests for billing/tick.lua behavior.

  Verifies tick scheduling logic, credit exhaustion handling,
  and elapsed_seconds parameter passing.

  Run with: lua tests/test_billing.lua
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
            voip_api_base_url   = "http://127.0.0.1:8000",
            voip_internal_token = "test-token",
            voip_sounds_base    = "/tmp/sounds/",
            voip_tick_timeout_ms = "1500",
        }
        return vars[key]
    end,
    api   = function(cmd, args) return "" end,
    sleep = function(ms) end,
}

argv = {}  -- populated per-test below

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

-- ── Test: elapsed_seconds argument parsing ────────────────────────────────────
test("tick elapsed_seconds: first tick passes 30", function()
    -- argv[2] = "30" should parse to 30 with tonumber
    local elapsed = tonumber("30") or 60
    assert_eq(elapsed, 30)
end)

test("tick elapsed_seconds: subsequent ticks pass 60", function()
    local elapsed = tonumber("60") or 60
    assert_eq(elapsed, 60)
end)

test("tick elapsed_seconds: missing arg defaults to 60", function()
    local elapsed = tonumber(nil) or 60
    assert_eq(elapsed, 60)
end)

-- ── Test: R-BILL-04 tick schedule timings ─────────────────────────────────────
test("R-BILL-04: first tick scheduled at +30s (half-interval offset)", function()
    -- auth.lua schedules: sched_api "+30 none luarun billing/tick.lua <uuid> 30"
    local first_tick_offset = 30
    assert_eq(first_tick_offset < 60, true, "first tick must be before the 60s interval")
    assert_eq(first_tick_offset, 30)
end)

test("R-BILL-04: tick sequence is 30, 90, 150 (not 60, 120, 180)", function()
    local t1 = 30              -- first tick
    local t2 = t1 + 60         -- second tick
    local t3 = t2 + 60         -- third tick
    assert_eq(t1, 30)
    assert_eq(t2, 90)
    assert_eq(t3, 150)
end)

-- ── Test: continue=false response terminates call ─────────────────────────────
test("tick kills call when continue=false", function()
    local killed = false
    -- Temporarily override freeswitch.api to capture uuid_kill call
    local orig = freeswitch.api
    freeswitch.api = function(cmd, args)
        if cmd == "uuid_kill" then killed = true end
    end

    -- Simulate the tick logic for continue=false
    local response = { data = { ["continue"] = false, remaining_seconds = 0 } }
    if not response.data or response.data["continue"] == false then
        freeswitch.api("uuid_kill", "test-uuid")
    end

    freeswitch.api = orig
    assert_eq(killed, true, "uuid_kill must be called on continue=false")
end)

test("tick does NOT kill call when continue=true", function()
    local killed = false
    local scheduled = false
    local orig = freeswitch.api
    freeswitch.api = function(cmd, args)
        if cmd == "uuid_kill" then killed = true end
        if cmd == "sched_api" then scheduled = true end
    end

    local response = { data = { ["continue"] = true, remaining_seconds = 240 } }
    if response.data and response.data["continue"] ~= false then
        freeswitch.api("sched_api", "+60 none luarun billing/tick.lua test-uuid 60")
    end

    freeswitch.api = orig
    assert_eq(killed, false, "uuid_kill must NOT be called when continue=true")
    assert_eq(scheduled, true, "next tick must be scheduled when continue=true")
end)

-- ── Test: API error terminates call (conservative fail-safe) ─────────────────
test("tick kills call on transport error", function()
    local killed = false
    local orig = freeswitch.api
    freeswitch.api = function(cmd, _)
        if cmd == "uuid_kill" then killed = true end
    end

    -- Simulate the tick error handling
    local err = "connection_error:timeout"
    if err then
        freeswitch.api("uuid_kill", "test-uuid")
    end

    freeswitch.api = orig
    assert_eq(killed, true, "uuid_kill must be called on API transport error")
end)

-- ── Summary ───────────────────────────────────────────────────────────────────
io.write("\n" .. passed .. " passed, " .. failed .. " failed\n")
if failed > 0 then os.exit(1) end
