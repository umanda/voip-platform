# Telecom Rules — Non-Negotiable Constraints

This file defines telecom-specific rules that override general software engineering preferences.
These rules exist because VoIP systems have real-time, financial, and regulatory consequences.

---

## SIP / Signaling Rules

### R-SIP-01: Never block the SIP signaling thread
Lua scripts called from FreeSWITCH dialplan run in the call's execution context.
Any blocking HTTP call must have a strict timeout (≤ 2000ms). On timeout:
- Log the failure with call UUID
- Route to a fallback IVR or return a 503 to the caller
- Never leave the call hanging silently

### R-SIP-02: Preserve all SIP headers
When bridging or transferring calls, always carry forward:
- `P-Asserted-Identity`
- `Remote-Party-ID`
- `X-Forwarded-For` (for Voxbone)
- `User-to-User`
Do not strip headers unless explicitly documented as safe.

### R-SIP-03: Handle all SIP response codes
FreeSWITCH Lua must handle these explicitly:
- 486 Busy Here
- 408 Request Timeout
- 503 Service Unavailable
- 404 Not Found (wrong number)
- 403 Forbidden (auth failure)
Each must produce appropriate CDR disposition codes.

### R-SIP-04: SIP re-INVITE support
Do not disable re-INVITE. It is used for:
- Call hold/resume
- Codec renegotiation
- DTMF mode switching

---

## Billing Rules

### R-BILL-01: Credit deduction must be atomic
Use Redis DECRBY with a Lua script. Pattern:
```lua
-- Redis Lua script (atomic)
local balance = redis.call('GET', KEYS[1])
if tonumber(balance) < tonumber(ARGV[1]) then
  return -1  -- insufficient
end
return redis.call('DECRBY', KEYS[1], ARGV[1])
```
Never: GET balance → check in application → SET new balance (race condition).

### R-BILL-02: CDRs are append-only
Once a CDR row is written as FINAL, it must never be updated.
Corrections are separate adjustment records.
This is a regulatory/audit requirement.

### R-BILL-03: Pre-call credit gate
Calculate minimum required credit before connecting:
```
min_credit = rate_per_minute × min_call_minutes (e.g., 1 minute)
```
Reject call if `current_balance < min_credit`.

### R-BILL-04: In-call credit monitoring
Billing ticks must occur before credit runs out, not after.
If tick interval is 60s, fire at t=30s, t=90s, t=150s... (not t=60, t=120...)
This prevents the "30-second hangup debt" problem.

### R-BILL-05: Credit reconciliation on crash
On billing worker startup:
1. Query Redis for all active `call:{uuid}` keys
2. Compare against FreeSWITCH active calls via ESL `show calls`
3. Any Redis session without a live FS call → finalize CDR immediately
4. Any live FS call without Redis session → re-register billing session

### R-BILL-06: Rate rounding
Always round per-second rates UP (ceiling), never floor.
Bill in whole seconds, never partial.

---

## Call Flow Rules

### R-FLOW-01: Auth API timeout behavior
If FastAPI /authorize does not respond in 2000ms:
- FreeSWITCH should route to error IVR (not drop silently)
- Log: `auth_timeout` with call UUID, caller ID, timestamp
- Do NOT retry — retries in call setup cause perceptible delay

### R-FLOW-02: Graceful call termination
When credit runs out, do NOT abruptly disconnect.
Sequence:
1. Play "Your credit is running low" prompt at 60s remaining
2. Play "Call will end in 10 seconds" at 10s remaining
3. Hangup cleanly (send SIP BYE, not RST)

### R-FLOW-03: No silent failures
Every error in the call path must produce:
- A CloudWatch log entry
- A CDR record (even for failed/unanswered calls)
- An appropriate SIP response to the caller

### R-FLOW-04: Concurrent call limits
Enforce per-account concurrent call limits in Redis:
```
Key: concurrent:{account_id}
Type: integer counter (INCR on answer, DECR on hangup)
```
Reject new calls if limit exceeded (return 503 with reason header).

---

## Infrastructure Rules

### R-INFRA-01: FreeSWITCH must have Elastic IP
Voxbone SIP trunks require a static IP for trunk registration.
The FreeSWITCH EC2 instance must have an Elastic IP allocated and associated.
This EIP must be stable across reboots.

### R-INFRA-02: RTP port range must be open
Security group must allow UDP 16384–32768 both inbound AND outbound.
Missing outbound RTP rules = one-way audio (common mistake).

### R-INFRA-03: FreeSWITCH config changes via hot-reload
Never restart FreeSWITCH during active calls.
Use: `fs_cli -x "reloadxml"` or `fs_cli -x "reload mod_lua"`
Deployments of Lua scripts require only `reload mod_lua`, not full restart.

### R-INFRA-04: ESL must be internal-only
FreeSWITCH ESL port (8021) must be:
- Bound to internal interface only
- Security group: allow only from billing worker ECS task SG
- Never exposed to internet

### R-INFRA-05: NTP sync is critical
FreeSWITCH and all services must use the same NTP source.
Time drift causes SIP authentication failures (nonce expiry).
Use AWS Time Sync Service (169.254.169.123) on all EC2 instances.

---

## Codec and Media Rules

### R-MEDIA-01: Codec negotiation order
Preferred codec order (unless account overrides):
1. G.711 PCMU (ulaw)
2. G.711 PCMA (alaw)
3. G.729 (if licensed)
4. Opus (for WebRTC only)

### R-MEDIA-02: DTMF handling
Default to RFC 2833 (telephone-event).
Some Voxbone routes require in-band DTMF detection as fallback.
Configure `mod_spandsp` for in-band detection.

---

## Logging Requirements

Every call-related log entry MUST include:
- `call_uuid` (FreeSWITCH UUID)
- `account_id`
- `caller_id` (E.164 format)
- `dialed_number` (E.164 format)
- `timestamp` (ISO 8601, UTC)
- `component` (lua/fastapi/billing-worker)
- `event_type` (call_start/auth_request/credit_check/hangup/etc.)

Use structured JSON logging. Never use `print()` or unstructured log lines.
