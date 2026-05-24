# Sofia (FreeSWITCH/Perl) — Legacy Audit
## Phase 0 — ifonix VoIP Platform Modernization
**Audited:** 2026-05-17 | **Source:** `/home/umanda/workplace/ifonix/sofia`

---

## 1. File Inventory & Purpose

| File | Role | Triggered By |
|------|------|-------------|
| `bin/checkServiceType.pl` | **Main entry point.** Answers call, validates destination, routes to service modules. Runs the full session lifecycle and posts final hangup CDR. | FreeSWITCH dialplan (mod_perl) |
| `bin/authentication.pl` | PIN authentication for customers and coaches. Collects 8-digit PIN via DTMF, validates with Sentinel. | `require`d from `checkServiceType.pl` |
| `bin/dialCoach.pl` | Bridges the call to the coach's phone number via Voxbone gateway. Manages recording start, ringing CDR, and outbound UUID lifecycle. | `require`d from `checkServiceType.pl` / `authentication.pl` |
| `bin/coachSessionHandler.pl` | Runs as a parallel `perlrun` process. Waits for coach to answer, then manages credit time-blocks and auto-hangup scheduling. | `$api->execute('perlrun', ...)` from `dialCoach.pl` |
| `bin/playAvailableTime.pl` | Runs as a parallel `perlrun` process. Plays available call time to caller before coach answers. | `$api->execute('perlrun', ...)` from `dialCoach.pl` |
| `bin/coachMenu.pl` | IVR menu for coach callers: check status, view earnings, toggle online/offline. | `require`d from `checkServiceType.pl` / `authentication.pl` |
| `bin/serviceDesk.pl` | Routes call to service desk agents with failover across multiple numbers. | `require`d from `checkServiceType.pl` / `authentication.pl` |
| `bin/servicedeskSessionHandler.pl` | Parallel process: waits for SD agent to answer, posts connected CDR. | `$api->execute('perlrun', ...)` from `serviceDesk.pl` |
| `bin/extension.pl` | Extension dial — customer enters 4-digit extension code after site number auth. | `require`d from `checkServiceType.pl` / `authentication.pl` |
| `bin/premium.pl` | Premium service handler. **Currently empty — no logic implemented.** | `require`d from `checkServiceType.pl` |
| `bin/recordingMgt.pl` | Batch script: uploads previous day's recordings to S3, reduces SD recording bitrate, deletes empty dirs. | Cron job (not dialplan) |
| `lib/ApiClient.pm` | HTTP client wrapper. All Sentinel API calls go through `postApi()` → `reqApi()` → `REST::Client`. | Used by all bin scripts |
| `lib/FsCommon.pm` | IVR helpers: TTS number playback, file_string assembly for `uuid_displace`, `leaveAMsg()`, `fsPrint()` console logger. | Used by all bin scripts |
| `lib/utils.pm` | Utility functions: `getConfValue()`, timestamp converters, `isNumber()`, recording path helpers. | Used by all bin scripts |
| `lib/AwsUploader.pm` | S3 upload via `Amazon::S3` Perl module. Uses hardcoded credentials from `sofia.conf`. | Used by `checkServiceType.pl` and `recordingMgt.pl` |
| `lib/MongoDbConnection.pm` | MongoDB log writer. **Currently disabled** — `addLog()` calls are commented out in `FsCommon.pm`. | Unused in production |
| `lib/sofia.conf` | INI config: Sentinel host/key, AWS credentials, provider names, sound paths, tuning params. **Contains live secrets.** | Read by all scripts via `getConfValue()` |
| `lib/id_rsa` | **RSA private key committed to git.** Used to SSH into the Sentinel host (10.0.0.20) to regenerate API tokens. | `sentinel_key_gen.sh` |
| `lib/sentinel_key_gen.sh` | Regenerates Sentinel OAuth client credentials via SSH. Exposes client_secret in script body. | Manual execution |
| `lib/new_key.txt` | Plain-text OAuth client credentials (Client ID: 4, Secret: hR6CRo...). | Output of `sentinel_key_gen.sh` |

---

## 2. Call Flow — Exact Sequence

```
PSTN Call Arrives (Voxbone DID)
  │
  ▼ FreeSWITCH mod_perl
checkServiceType.pl
  │
  ├─ setVariable: hangup_after_bridge=false, continue_on_fail=true, ignore_early_media=true
  ├─ setVariable: provider_id=1 (HARDCODED)
  ├─ fixCliNumbers() → strip '+' from src and dst
  ├─ session->sleep(500ms)
  ├─ session->answer()
  ├─ session->sleep(500ms)
  │
  ├─ POST /api/v1/call/validate  ←── Sentinel API call #1
  │     body: {call_id, src_number, dst_number, info="Session start", provider_id, timestamp}
  │
  ├─ setLanguage() → sets $lang, $sounds path, $digit_path
  │
  ├─ if validate_dst == false → play something_wrong.mp3 → hangup
  │
  └─ Route by service_type:
       ├─ 1 (credit/site_number)
       │    ├─ if direct_dial_auth → require extension.pl
       │    └─ else → require authentication.pl (8-digit PIN)
       │
       ├─ 2 (direct dial)
       │    ├─ if direct_dial_auth AND credit_status AND coach online → require dialCoach.pl
       │    ├─ if direct_dial_auth AND no credit → play creditsnone.mp3 → askServiceDesk()
       │    └─ else → handleConsultantStatus() then require authentication.pl
       │
       ├─ 3 (service-desk)
       │    └─ handleConsultantStatus() then require serviceDesk.pl (or leaveAMsg if offline)
       │
       ├─ 4 (coach portal)
       │    ├─ if direct_dial_auth → set coach_auth_response, require coachMenu.pl
       │    └─ else → require authentication.pl (coach mode)
       │
       └─ 5 (premium) → require premium.pl (EMPTY — no-op)

  │
  ├─ Hangup handler (always runs):
  │    ├─ Determine hangup_info from last_bridge_hangup_cause
  │    ├─ POST /api/v1/call/status (call_status=7=end_time)   ←── CDR write
  │    └─ uploadRecording() → S3
  │
  └─ END
```

**Parallel processes spawned by `dialCoach.pl`:**
- `perlrun playAvailableTime.pl <uuid> <lang>` — plays remaining time
- `perlrun coachSessionHandler.pl <uuid> <lang>` — manages time-blocks

---

## 3. HTTP API Calls — Exact Request Format

All calls: `POST`, `Content-Type: application/json`, `Authorization: Bearer <JWT>`, to `http://10.0.0.49/api/v1`.

### 3.1 POST `/call/validate` (destination routing)
```json
{
  "call_id":     "<FreeSWITCH UUID>",
  "src_number":  "<caller_id, + stripped>",
  "dst_number":  "<dialed number, + stripped>",
  "info":        "Session start",
  "provider_id": "1",
  "timestamp":   "2024-01-15 10:23:45"
}
```

### 3.2 POST `/customer/call/confirm` (customer PIN auth)
```json
{
  "credit_code": "<8-digit PIN entered by customer>",
  "src_number":  "<caller_id>",
  "dst_number":  "<dialed number>",
  "call_id":     "<UUID>",
  "provider_id": "1",
  "info":        "auth",
  "timestamp":   "2024-01-15 10:23:50"
}
```

### 3.3 POST `/coach/call/confirm` (coach PIN auth)
Same body as `/customer/call/confirm` — endpoint differs.

### 3.4 POST `/call/status` (lifecycle events)
```json
{
  "extension":      0,
  "user_id":        "<from session var>",
  "customer_id":    "<from session var>",
  "timestamp":      "2024-01-15 10:24:00",
  "call_id":        "<UUID>",
  "number_type_id": 2,
  "call_status":    3,
  "provider_id":    "1",
  "src_number":     "<caller_id>",
  "dst_number":     "<destination>",
  "info":           "Ringing",
  "call_summary":   1
}
```
`call_status` values: 1=start, 3=ringing, 4=connected, 6=hangup, 7=end_time
`number_type_id`: 1=site, 2=direct, 3=service-desk, 4=coach

### 3.5 POST `/service-desk/call/status`
Same body as `/call/status` but without `extension`, `user_id`, `customer_id` fields.

### 3.6 POST `/check/call/time` (time-block renewal, every ~295s)
```json
{
  "timestamp": "2024-01-15 10:28:55",
  "call_id":   "<UUID>",
  "info":      "Call time block update"
}
```

### 3.7 POST `/service-desk/active/list`
```json
{
  "language_id": 4
}
```

### 3.8 POST `/toggle/coach/status`
```json
{
  "consultant_id": 42,
  "call_id":       "<UUID>"
}
```

### 3.9 POST `/coach/extension`
```json
{
  "extension":    "1234",
  "dst_number":   "<dialed number>",
  "src_number":   "<caller_id>",
  "credit_code":  "<accountcode>",
  "call_id":      "<UUID>",
  "provider_id":  "1",
  "info":         "extension 1234",
  "timestamp":    "2024-01-15 10:24:10"
}
```

---

## 4. Response Fields Used By Sofia

### `/call/validate` response (via `response->{body}`)
| Field | Type | Used For |
|-------|------|----------|
| `validate_dst` | bool | Gate: false = play error, hangup |
| `service_type` | int | Routing: 1-5 |
| `direct_dial_auth` | bool | Skip PIN auth if true |
| `credit_status` | bool | Gate: false = play creditsnone.mp3 |
| `consultant_status` | int | 1=online→proceed, 2=busy→play busy.mp3, 3=offline→play notavailable.mp3 |
| `lang` | int | 1=nl, 2=fr, 3=es, 4=en → sets sound file paths |
| `customer.customer_id` | int | Set session var `customer_id` |
| `customer.user_id` | int | Set session var `user_id` |
| `customer.group_id` | int | Set session var `group_id` |
| `customer.pin` | str | Set session var `accountcode` (for pre-auth direct dial) |
| `consultant.dst_number` | str | Set session var `coach_number` (outbound dial target) |
| `time.avb_time` | int | Set session var `avb_time` (seconds of credit available) |
| `time.slot_value` | int | Set session var `time_block` (max 300s per block) |
| `earnings` | object | Coach menu: today/thisMonth/lastMonth earnings |
| `consultant_id` | int | Stored in `coach_auth_response` for coach menu |

### `/customer/call/confirm` response
| Field | Used For |
|-------|----------|
| `validate_pin` | bool: false = play creditcodewrong.mp3, retry |
| `credit_status` | bool: false = play creditsnone.mp3 |
| `consultant.dst_number` | Set `coach_number` |
| `time.avb_time` / `time.slot_value` | Set time block vars |

### `/check/call/time` response
| Field | Used For |
|-------|----------|
| `time.slot_value` | New time block duration (0 = no credit left → hangup) |
| `time.avb_time` | Updated available seconds |

---

## 5. Error Handling (What Happens When Things Go Wrong)

| Scenario | Behavior |
|----------|----------|
| Sentinel unreachable / timeout | **None.** `REST::Client` has no timeout configured. Call hangs until TCP timeout (~minutes). |
| Empty HTTP response from Sentinel | `reqApi()` runs `ssh -h` (debug artifact), returns SSH help text. Script receives a string instead of hashref → Perl dies with "Not a HASH reference" or silently treats falsy. Call behavior undefined. |
| Invalid JSON response | Returns string `"Invalid json"` → same undefined behavior as above. |
| `validate_dst` = false | Plays `something_wrong.mp3`, sets hangup_info, posts CDR with call_status=7. |
| `validate_pin` = false | Plays `creditcodewrong.mp3`, retries up to 3 times. After 3 fails: play `login_failed.mp3`, offer service-desk. |
| PIN entry timeout (5000ms) | `play_and_get_digits` returns empty `accountcode`. Script loops back to ATTEMPT. |
| Consultant busy (status=2) | Plays `busy.mp3`, sets `hangup_info`, falls through to CDR write. |
| Consultant offline (status=3) | Plays `notavailable.mp3`, similar. |
| No credits | Plays `creditsnone.mp3`. Asks if customer wants service-desk (press 1). |
| Credit runs out during call | `coachSessionHandler.pl`: gets slot_value=0, sets `ran_out_credits=1`, sleeps 6s. FreeSWITCH `sched_hangup` fires (set to `alotted_timeout`). Hangup handler sees `ran_out_credits=1`, posts CDR with info="Ran out credits". |
| Coach no answer | `dialCoach.pl`: `NO_ANSWER` hangup cause → play `noresponse.mp3`. |
| Coach rejected | `CALL_REJECTED` → play `busy.mp3`, call_summary=7. |
| Coach number unreachable | `UNALLOCATED_NUMBER` → play `destination_unreachable.mp3`, call_summary=6. |
| All service-desk agents unavailable | Iterates list, if all return NO_ANSWER/USER_BUSY: plays `notavailable_sd.mp3`, calls `leaveAMsg()` (voice message recording). |

---

## 6. FreeSWITCH Channel Variables Set by Sofia

| Variable | Value | Purpose |
|----------|-------|---------|
| `hangup_after_bridge` | false | Keep inbound session alive after bridge drops |
| `continue_on_fail` | true | Don't auto-hangup on bridge failure |
| `ignore_early_media` | true | Don't wait for early media |
| `provider_id` | 1 (hardcoded) | Passed to Sentinel in all requests |
| `service_type` | credit/direct/service-desk/coach/premium/unknown | Human-readable type |
| `service_type_id` | 1-5 | Numeric service type |
| `user_id` | From Sentinel | Customer's user record ID |
| `customer_id` | From Sentinel | Customer's credit record ID |
| `group_id` | From Sentinel | Group/tenant ID |
| `accountcode` | PIN entered or pre-auth PIN | Used by coachMenu for toggle |
| `coach_number` | From Sentinel | E.164 without + |
| `avb_time` | From Sentinel (seconds) | Available credit seconds |
| `time_block` | From Sentinel (seconds, max 300) | Current block duration |
| `ran_out_credits` | 0/1 | Set by coachSessionHandler when credits = 0 |
| `less_than_benchmark` | 0/1 | Set when credit < benchmark (30s) |
| `gen_outbound_uuid1/2/3` | Generated UUIDs | Pre-allocated UUIDs for outbound legs |
| `file_string` | Sound file chain | For uuid_displace available time announcement |
| `rec_dir` | YYYYMMDD[/sd] | S3 path component for recording upload |
| `last_bridge_hangup_cause` | FS cause code | Used for CDR info string |
| `hangup_time` / `dst_hangup_time` | datetime | CDR timestamps |
| `lang` | nl/fr/es/en | Language code |
| `lang_id` | 1-4 | Language ID for Sentinel |
| `servicedesk` | Phone number | SD number being dialed |
| `consultant_status` | 1/2/3 | Stored for coach menu display |
| `ringback` | ${fr-ring} | French ring tone to caller |

---

## 7. Audio Files Referenced

Base paths set in `sofia.conf` → e.g., `/usr/share/freeswitch/sounds/galaxy/en/`

Languages supported: `en`, `fr`, `nl` (es defined in code but no sound path in config)

Core customer-facing sounds: `creditsmenu.mp3`, `creditsnone.mp3`, `creditcodewrong.mp3`, `login_success.mp3`, `login_failed.mp3`, `empty_pin.mp3`, `support_option.mp3`, `support_option_or_end.mp3`, `something_wrong.mp3`, `zero_to_continue.mp3`, `empty_response.mp3`, `invalid_option.mp3`, `invalid_option_tryagain.mp3`, `password_saved.mp3`, `password_not_save.mp3`, `busy.mp3`, `notavailable.mp3`, `destination_unreachable.mp3`, `noresponse.mp3`, `creditslow_8000.mp3`, `count-down-beep.mp3`

Extension sounds: `enter_extension_number.mp3`, `correct_ext.mp3`, `incorrect_ext.mp3`, `empty_extension.mp3`, `extension_failed.mp3`

Coach sounds: `coach_menu.mp3`, `coach_change_status_online.mp3`, `coach_change_status_offline.mp3`, `status_online.mp3`, `status_busy.mp3`, `status_offline.mp3`, `current_status_is.mp3`, `coach_status_been_changed.mp3`, `coach_status_did_not_change.mp3`, `today.mp3`, `this_month.mp3`, `last_month.mp3`, `you_had.mp3`, `you_earned.mp3`, `total_conversation_time.mp3`, `and.mp3`, `hours.mp3`, `minutes.mp3`, `seconds.mp3`

Service desk sounds: `notavailable_sd.mp3`, `noresponse_sd.mp3`, `busy_sd.mp3`, `leave_your_msg.mp3`

Digit sounds: separate path, files named `0.mp3`–`9.mp3`, `10.mp3`–`19.mp3`, `20.mp3`, `30.mp3`...`90.mp3`, `hundred.mp3`, `thousand.mp3`, `million.mp3`

---

## 8. Hardcoded Values Found

| Item | Location | Value | Risk |
|------|----------|-------|------|
| AWS Access Key | `lib/sofia.conf` | `REDACTED_AWS_ACCESS_KEY` | CRITICAL |
| AWS Secret Key | `lib/sofia.conf` | `REDACTED_AWS_SECRET_KEY` | CRITICAL |
| Sentinel Bearer JWT | `lib/sofia.conf` | `eyJ0eXAi...` (expired Jan 2022) | CRITICAL |
| RSA private key | `lib/id_rsa` | Full PEM key in git | CRITICAL |
| Sentinel host | `lib/sofia.conf` | `http://10.0.0.49` (plaintext HTTP, private IP) | HIGH |
| MongoDB password | `lib/sofia.conf` | `admin123` | HIGH |
| OAuth client secret | `lib/sentinel_key_gen.sh` | `REDACTED_OAUTH_SECRET_1` | HIGH |
| New OAuth credentials | `lib/new_key.txt` | Client ID 4, secret `hR6CRo...` | HIGH |
| Provider ID | `checkServiceType.pl:41` | `1` (always) | MEDIUM |
| All providers same | `sofia.conf` | All 3 providers = `voxbone-outbound` | MEDIUM |
| Originate timeout | `dialCoach.pl:186` | `30` seconds | LOW |
| Service desk call timeout | `sofia.conf` | `30` seconds | LOW |
| Credit warn threshold | `sofia.conf` | `320` seconds | LOW |
| Beep countdown start | `sofia.conf` | `10` seconds before end | LOW |

---

## 9. Credit/Billing Timing Detail

```
Call connects at t=0 (bridge_time)
  → Initial avb_time and slot_value from /call/validate or /customer/call/confirm
  → coachSessionHandler enters TIMELOOP

TIMELOOP iteration:
  → sched_hangup set for t=(spent_time + time_block) seconds from bridge
  → if avb_time < 320: play creditslow_8000.mp3
  → sleep until t=(spent_time + time_block - 5)    ← 5s before block expires
  → spent_time += time_block
  → POST /check/call/time with timestamp=(bridge_time + spent_time)
  → receive new {slot_value, avb_time}
  → if slot_value == 0: set ran_out_credits=1, sleep 6s → exit (auto-hangup fires)
  → goto TIMELOOP

Hangup:
  → checkServiceType.pl posts to /call/status (call_status=6, DST hangup)
  → then posts /call/status (call_status=7, end_time)
```

**Gap:** The 5-second buffer before block expiry is the reconciliation window. If the API call to `/check/call/time` takes >5 seconds (e.g., DB slow), the call gets auto-hung up by `sched_hangup` and the new block may be missed.

---

## 10. Completion Check Answers

- **What JSON body does Sofia send to Sentinel?** — See Section 3 above. POST with JSON, Bearer auth.
- **What does Sentinel return when credit is insufficient?** — `credit_status: false` in `/call/validate` or `/customer/call/confirm` response. Sofia plays `creditsnone.mp3`.
- **How long does Sofia wait before timing out on the API?** — **No timeout configured.** `REST::Client` uses TCP default (system-level, potentially minutes).
- **What FreeSWITCH actions after successful auth?** — Sets `coach_number` session var, spawns `playAvailableTime.pl` and `coachSessionHandler.pl` as background processes, starts recording, dials coach via `sofia/gateway/voxbone-outbound/+<number>`.
- **Where are CDRs written and what fields?** — Written to `statistics` table in PostgreSQL via Sentinel PHP. Fields listed in schema-map.md. Event log in `tracings` table (append-only).
- **What happens if Sentinel is unreachable during a live call?** — `/check/call/time` call fails silently (returns error string). `session->setVariable("time_block", ...)` receives an error string, not a number. `isNumber()` returns false for slot_value → **TIMELOOP exits with `hangup_info = "Invalid avb time slot from sofia"`**. Call is terminated. Customer loses remaining credit for the current block. No CDR error event.
