--[[
  lib/http.lua — HTTP POST client with mandatory timeout.

  Uses luasocket (socket.http + ltn12) which is included in standard
  FreeSWITCH builds. cjson is available as a FreeSWITCH built-in.

  Migration Notes:
    Legacy: REST::Client with no timeout (CRIT-05 in risk-findings.md).
            Calls could hang indefinitely if Sentinel was slow.
    New:    socket.http.TIMEOUT enforces a hard ceiling (R-SIP-01).
            Default 2000ms for auth, 1500ms for billing ticks.

  Telecom note:
    HTTP errors in the call path must NEVER cause silent failures.
    Callers must always receive an audible response (error IVR or hangup
    with cause). The caller decides what to do with nil/err returns.

  Accepted HTTP status codes that carry structured JSON:
    200 — success
    401 — account not found
    402 — insufficient credit
    403 — account suspended
    404 — DID not found
    500 — internal error (billing tick always returns 200, so 500 is fatal)
]]

local M = {}

local socket_http = require("socket.http")
local ltn12       = require("ltn12")
local cjson       = require("cjson")

-- Statuses that carry a parseable JSON body from FastAPI
local JSON_STATUSES = { [200]=true, [401]=true, [402]=true, [403]=true, [404]=true }

function M.post(url, body, token, timeout_ms)
    local timeout_s = ((timeout_ms or 2000) / 1000)

    local ok_enc, encoded = pcall(cjson.encode, body)
    if not ok_enc then
        return nil, "json_encode_error"
    end

    -- socket.http.TIMEOUT is a global — set before each request (R-SIP-01)
    socket_http.TIMEOUT = timeout_s

    local resp_chunks = {}
    local req_headers = {
        ["Content-Type"]   = "application/json",
        ["Content-Length"] = tostring(#encoded),
    }
    if token and token ~= "" then
        req_headers["Authorization"] = "Bearer " .. token
    end

    local ok, code, _ = socket_http.request({
        url     = url,
        method  = "POST",
        headers = req_headers,
        source  = ltn12.source.string(encoded),
        sink    = ltn12.sink.table(resp_chunks),
    })

    if not ok then
        -- luasocket returns nil + error string on timeout or connection failure
        return nil, "connection_error:" .. tostring(code)
    end

    if not JSON_STATUSES[code] then
        return nil, "unexpected_status:" .. tostring(code)
    end

    local raw = table.concat(resp_chunks)
    local ok_dec, parsed = pcall(cjson.decode, raw)
    if not ok_dec then
        return nil, "json_decode_error"
    end

    return parsed, nil
end

return M
