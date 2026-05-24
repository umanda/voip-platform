--[[
  lib/utils.lua — Phone number normalization and misc helpers.

  Migration Notes:
    Legacy: fixCliNumbers() in checkServiceType.pl stripped '+' before every
            Sentinel API call. The DB stores numbers WITHOUT '+'.
    New:    normalize_e164() does the same stripping. Always call this before
            passing numbers to the API or comparing against DB values.
]]

local M = {}

-- Strip leading '+' — DB and FastAPI store numbers without it.
-- Input:  "+33612345678" or "33612345678" or nil
-- Output: "33612345678" or ""
function M.normalize_e164(number)
    if not number or number == "" then return "" end
    return tostring(number):gsub("^%+", "")
end

-- Add '+' back for SIP dial strings (sofia/gateway/name/+number).
function M.to_sip_uri_number(number)
    if not number or number == "" then return "" end
    local n = tostring(number):gsub("^%+", "")
    return "+" .. n
end

function M.is_numeric(s)
    if not s then return false end
    return tostring(s):match("^%d+$") ~= nil
end

-- ISO 8601 UTC timestamp — used in CDR fields.
function M.iso_now()
    return os.date("!%Y-%m-%dT%H:%M:%SZ")
end

-- Map language_id (from API) to the sound path subdirectory name.
-- Matches legacy setLanguage() in checkServiceType.pl.
-- 1=nl, 2=fr, 3=es, 4=en (es has no sound path — falls back to en)
local LANG_MAP = { [1]="nl", [2]="fr", [3]="en", [4]="en" }
function M.lang_dir(language_id)
    return LANG_MAP[tonumber(language_id)] or "en"
end

return M
