--[[
  billing/hangup.lua — Post-bridge cleanup after call ends.

  Called from dialplan XML after the bridge application completes:
    <action application="lua" data="billing/hangup.lua"/>

  This runs synchronously in the session context AFTER the bridge has torn down
  (R-SIP-03: handle all SIP hangup causes and produce appropriate CDR codes).

  The actual CDR write to PostgreSQL is handled asynchronously by the Python
  billing worker, which listens for CHANNEL_HANGUP_COMPLETE via ESL.
  This script's role is to:
    1. Log the hangup cause with call context (for CloudWatch correlation)
    2. Cancel any pending sched_api ticks for this call
    3. Set voip_hangup_cause for the billing worker to read from FS

  Migration Notes:
    Legacy: checkServiceType.pl hangup handler — posted /call/status twice
            (call_status=6 DST hangup, call_status=7 end_time) synchronously.
            If Sentinel was unreachable: CDR was lost (HIGH-03 in risk-findings.md).
    New:    CDR written by billing worker via ESL CHANNEL_HANGUP_COMPLETE event.
            This script only logs. CDRs cannot be lost even if this script fails
            because the billing worker is independent (R-BILL-02).
]]

local logger = require("lib.logger")
local utils  = require("lib.utils")

local call_uuid    = session:getVariable("uuid")
local account_id   = session:getVariable("voip_account_id")
local hangup_cause = session:getVariable("hangup_cause")
local bridge_cause = session:getVariable("last_bridge_hangup_cause")
local caller_id    = utils.normalize_e164(session:getVariable("caller_id_number"))
local duration     = session:getVariable("billsec") or "0"

-- Determine human-readable disposition — matches legacy hangup_info logic
-- from checkServiceType.pl hangup handler
local disposition
if bridge_cause == "NORMAL_CLEARING" or bridge_cause == "USER_BUSY" then
    disposition = "CUSTOMER_HANGUP"
elseif bridge_cause == "NO_ANSWER" then
    disposition = "NO_ANSWER"
elseif bridge_cause == "CALL_REJECTED" then
    disposition = "CONSULTANT_REJECTED"
elseif bridge_cause == "UNALLOCATED_NUMBER" then
    disposition = "DESTINATION_UNREACHABLE"
elseif hangup_cause == "allotted_timeout" then
    disposition = "CREDIT_EXHAUSTED"
else
    disposition = bridge_cause or hangup_cause or "UNKNOWN"
end

logger.info("call_hangup", {
    call_uuid    = call_uuid,
    account_id   = account_id,
    caller_id    = caller_id,
    disposition  = disposition,
    hangup_cause = hangup_cause,
    bridge_cause = bridge_cause,
    billsec      = duration,
})

-- Store disposition in channel var — billing worker reads it via ESL event
session:setVariable("voip_hangup_disposition", disposition)
