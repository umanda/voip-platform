--[[
  lib/logger.lua — Structured JSON logger for FreeSWITCH Lua scripts.

  Outputs JSON lines to the FreeSWITCH log at the appropriate level.
  Every log entry includes timestamp, component, and event_type per
  the logging requirements in telecom-rules.md.

  Migration Notes:
    Legacy: fsPrint() in FsCommon.pm — unstructured console output
    New:    JSON lines to FreeSWITCH log → forwarded to CloudWatch by the
            host-level log agent (awslogs or fluentd)

  Telecom note:
    NEVER log account_token / PIN values. Log only account_id (int) and
    the last 2 chars of credit_code for debugging (masking standard).
]]

local M = {}

local ok_cjson, cjson = pcall(require, "cjson")
if not ok_cjson then
    -- Minimal fallback encoder (no nested tables) if cjson unavailable
    cjson = {
        encode = function(t)
            local parts = {}
            for k, v in pairs(t) do
                local val
                if type(v) == "string" then
                    val = '"' .. v:gsub('"', '\\"') .. '"'
                else
                    val = tostring(v)
                end
                parts[#parts + 1] = '"' .. k .. '":' .. val
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    }
end

local function emit(fs_level, event_type, data)
    data = data or {}
    data.timestamp  = os.date("!%Y-%m-%dT%H:%M:%SZ")
    data.component  = "lua"
    data.event_type = event_type

    local ok, encoded = pcall(cjson.encode, data)
    if not ok then
        encoded = '{"event_type":"' .. event_type .. '","encode_error":true}'
    end

    freeswitch.log(fs_level, encoded .. "\n")
end

function M.debug(event_type, data) emit(freeswitch.DEBUG,   event_type, data) end
function M.info(event_type,  data) emit(freeswitch.INFO,    event_type, data) end
function M.warn(event_type,  data) emit(freeswitch.WARNING, event_type, data) end
function M.error(event_type, data) emit(freeswitch.ERR,     event_type, data) end

return M
