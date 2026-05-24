# Sentinel (PHP/Laravel API) — Legacy Audit
## Phase 0 — ifonix VoIP Platform Modernization
**Audited:** 2026-05-17 | **Source:** `/home/umanda/workplace/ifonix/galaxy.2.0/helios/platform/Sites/Sentinel`

---

## 1. Authentication Mechanism

Sentinel uses **Laravel Passport OAuth2 client credentials** grant:
- Middleware: `['client']` wraps all IVR routes
- Sofia sends: `Authorization: Bearer <JWT>` in every request
- JWT payload contains `aud` (client ID), `jti` (token ID), `iat`, `nbf`, `exp`, `sub`, `scopes`
- **The JWT in sofia.conf expired January 11, 2022** (`exp: 1641878369`)
- Token regeneration: SSH from FreeSWITCH server to Sentinel host using `id_rsa`, runs Laravel command, output written to `new_key.txt`
- No per-request validation beyond the Passport middleware (no IP allowlist, no rate limiting observed)

---

## 2. All API Endpoints

Base prefix: `/api/v1` — all are `POST` methods.

| Method | Path | Controller | Repository Method | Purpose |
|--------|------|-----------|-------------------|---------|
| POST | `/call/validate` | `CallValidateController@checkValidity` | `CallValidateRepository::fetch()` | Validate destination number, return routing and credit data |
| POST | `/customer/call/confirm` | `CustomerAuthController@checkValidity` | `CustomerAuthRepository::fetch()` | Validate customer 8-digit PIN |
| POST | `/customer/auth` | `CustomerAuthController@store` | `BaseRepository::ivrKnownUsers()` | Save phone→user mapping for future direct-dial pre-auth |
| POST | `/check/customer/credits` | `CustomerActionsController@get` | `CustomerActionsRepository::currentCredits()` | Get current credit balance by PIN |
| POST | `/call/status` | `CustomerActionsController@store` | `CustomerActionsRepository::callLogs()` | Log call lifecycle events (ringing/connected/hangup) |
| POST | `/check/call/time` | `CustomerActionsController@fetch` | `CustomerActionsRepository::availableCallBlock()` | Get next credit time-block, deduct from balance |
| POST | `/service-desk/active/list` | `ServiceDeskController@list` | `ServiceDeskRepository::active()` | List online SD agents by language |
| POST | `/service-desk/call/status` | `ServiceDeskController@store` | `ServiceDeskRepository::callLogs()` | Log SD call lifecycle events |
| POST | `/coach/auth` | `CoachAuthController@store` | `BaseRepository::ivrKnownUsers()` | Save coach phone mapping |
| POST | `/coach/call/confirm` | `CoachAuthController@checkValidity` | `CoachAuthRepository::fetch()` | Validate coach PIN, return earnings |
| POST | `/toggle/coach/status` | `CoachActionsController@toggle` | `CoachActionsRepository::status()` | Toggle coach IVR status online↔offline |
| POST | `/coach/extension` | `CallValidateController@checkCoachExtension` | `CallValidateRepository::fetchCoach()` | Validate 4-digit extension code, return coach routing |

**Chat routes** (`/api/v1/chat/*`) — separate concern, not Sofia-facing.

**Response envelope (all endpoints):**
```json
{
  "api": {
    "version": "1.0",
    "content_type": "application/json",
    "status": 200,
    "method": "POST",
    "created_at": "2024-01-15 10:23:45",
    "id": "<request fingerprint>",
    "context": "ivr"
  },
  "body": { <actual data> }
}
```
Sofia reads only `$response->{body}`. The `message` key is stripped in every controller before returning.

---

## 3. Endpoint Schemas — Request & Response

### POST `/call/validate`
**Request:** `{call_id, src_number, dst_number, info, provider_id, timestamp}`
**Validator:** checks all fields present, timestamp format `Y-m-d H:i:s`

**Business logic:**
1. Look up `site_ivr_numbers` by `dst_number`
2. If type=1 (site/credit): handleExtensionDial → look for pre-authed user by `src_number` in `ivr_known_users`
3. If type=2 (direct): handleDirectNumber → check `ivr_known_users`, load credit customer, calculate available time
4. If type=3 (service-desk): handleServiceDeskNumber → validate consultant online
5. If type=4 (coach desk): handleCoachDeskNumber → validate consultant via `src_number` lookup

**Response body:**
```json
{
  "validate_dst": true,
  "service_type": 2,
  "direct_dial_auth": true,
  "credit_status": true,
  "lang": 4,
  "country": 12,
  "customer": {
    "pin": "12345678",
    "customer_id": 101,
    "user_id": 55,
    "group_id": 3
  },
  "consultant": {
    "dst_number": "33612345678",
    "providers": ["voxbone-outbound"]
  },
  "time": {
    "avb_time": 1800,
    "slot_value": 300
  },
  "earnings": {
    "today": {"total_earnings": 12.50, "conversation_time": 3600},
    "thisMonth": {"total_earnings": 450.00, "conversation_time": 86400},
    "lastMonth": {"total_earnings": 620.00, "conversation_time": 112000}
  },
  "consultant_id": 42
}
```
If `validate_dst=false`, only `{"validate_dst": false}` is returned.

**DB operations:**
- `SELECT` on `site_ivr_numbers` by number
- `SELECT` on `ivr_known_users` by src_number
- `SELECT` on `credits_customers` JOIN countries
- `SELECT` on `consultant_ivr_numbers` JOIN consultants JOIN consultant_phone_numbers
- `SELECT` on `currency_exchange_rates` (for FX conversion)
- `INSERT` on `statistics` (creates CDR record at call start)
- `INSERT` on `tracings` (start_time event + auth event)
- `UPDATE` on `credits_customers` (deducts first time-block credit)

---

### POST `/customer/call/confirm`
**Request:** `{credit_code, dst_number, call_id, src_number, provider_id, info, timestamp}`

**Business logic:**
1. Look up `credits_customers` by `credit_code` (8-digit PIN)
2. Validate: not blocked, not deleted, user active, not user-deleted
3. Check country `direct_number_enabled` vs site type
4. Calculate call rate with VAT and FX conversion
5. Compute `avb_time` from `current_credits / rate_per_second`
6. Deduct first time-block from `credits_customers.current_credits`
7. Update existing `statistics` record with customer details

**Response body:**
```json
{
  "validate_pin": true,
  "consultant_status": 1,
  "credit_status": true,
  "customer": {
    "credit_code": "12345678",
    "customer_id": 101,
    "user_id": 55,
    "group_id": 3
  },
  "consultant": { "dst_number": "33612345678" },
  "time": { "avb_time": 900, "slot_value": 300 }
}
```
If `validate_pin=false`: `{"validate_dst": false, "validate_pin": false}`

---

### POST `/check/call/time`
**Request:** `{call_id, timestamp, info}`

**Business logic:**
1. Look up `statistics` by `unique_id`
2. Fetch `credits_customer.current_credits`
3. Recalculate rate with VAT+FX
4. Compute new `avb_time` and `slot_value` (min of avb_time and 300)
5. If `slot_value > 0`: deduct `slot_value` seconds worth of credit from `credits_customers`
6. Write `tracings` row with status=`credit_block_updated(5)`

**Response body:**
```json
{
  "time": {
    "avb_time": 600,
    "slot_value": 300
  }
}
```
If credit exhausted: `{"time": {"avb_time": 0, "slot_value": 0}}`

---

### POST `/call/status`
**Request:** `{extension, user_id, customer_id, timestamp, call_id, number_type_id, call_status, provider_id, src_number, dst_number, info, call_summary}`

**Business logic by `call_status`:**
- `3` (ringing): save tracing, update `statistics.ringing_start_time`
- `4` (connected): save tracing, set `consultants.ivr_status = 2` (busy), update `statistics.connected_time`
- `6` (hangup) / `7` (end): full billing reconciliation:
  - If `conversation_duration > call_minimum_bench_mark_time (30s)`: bill actual seconds, refund/charge difference
  - Else: refund the pre-deducted block amount (short call)
  - Update `statistics` with all duration fields and final credit values
  - Set `consultants.ivr_status = 1` (online)
  - Update `consultant_statistics.no_of_consultations` and `total_call_duration`

**Response body:** `{"status": true}` or `{"status": false}`

---

### POST `/service-desk/active/list`
**Request:** `{language_id}`

**Response body:**
```json
[
  {"dst_number": "33698765432", "ivr_number": "33123456789"},
  {"dst_number": "33612223344", "ivr_number": "33123456790"}
]
```
Returns all online SD agents for the given language. Empty array if none.

---

### POST `/coach/call/confirm`
**Request:** Same as `/customer/call/confirm`

**Business logic:**
1. Look up `credits_customers` by `credit_code` — coach uses same 8-digit code as customer account
2. Navigate `credits_customers → user → consultant`
3. Validate consultant not blocked/deleted
4. Update `statistics.consultant_id`
5. Calculate earnings (today, thisMonth, lastMonth) from `statistics` aggregates

**Response body:**
```json
{
  "validate_pin": true,
  "earnings": {
    "today": {"total_earnings": 12.50, "conversation_time": 3600},
    "thisMonth": {"total_earnings": 450.00, "conversation_time": 86400},
    "lastMonth": {"total_earnings": 620.00, "conversation_time": 112000}
  },
  "consultant_status": 1,
  "currency": "euros",
  "consultant_id": 42
}
```
Currency format: `eur` → `"euros"`, `chf` → `"chfs"`, `cad` → `"cads"` (used as sound file prefix).

---

### POST `/toggle/coach/status`
**Request:** `{consultant_id, call_id}`

**Logic:** Toggle `consultants.ivr_status` between 1 (online) and 3 (offline). If status was 1 → set 3. If not 1 → set 1.

**Response:** `{"status": true}` or `{"status": false}`

---

## 4. Database Models & Tables

| Eloquent Model | Table | Key Columns |
|---------------|-------|-------------|
| `Statistics` | `statistics` | See below — the main CDR table |
| `Tracings` | `tracings` | `statistics_id, timestamp, status, info, credit_before, credit_after` |
| `SiteIvrNumber` | `site_ivr_numbers` | `number, type_id, language_id, country_id, group_id` |
| `ConsultantIvrNumber` | `consultant_ivr_numbers` | `consultant_id, site_ivr_number_id, group_id` |
| `Consultant` | `consultants` | `id, ivr_status, call_rate, currency_code, commission_percentage, is_blocked, is_deleted, provider_sequence` |
| `ConsultantPhoneNumber` | `consultant_phone_numbers` | `consultant_id, phone_number, is_active, surcharge_amount` |
| `ConsultantExtensionDetail` | `consultant_extension_details` | `consultant_id, group_id, extension` |
| `ConsultantStatistics` | `consultant_statistics` | `consultant_id, group_id, no_of_consultations, total_call_duration` |
| `CreditsCustomer` | `credits_customers` | `id, user_id, credit_code, current_credits, currency_code, is_blocked, is_deleted` |
| `IvrKnownUsers` | `ivr_known_users` | `number, user_id` |
| `Country` | `countries` | `id, name, currency_code, effective_vat_rate, direct_number_enabled` |
| `CurrencyExchangeRate` | `currency_exchange_rates` | JSON blob per currency with `rates` sub-object |
| `User` | `users` | `id, status, is_deleted, group_id` |
| `Role` / `RoleDivision` | `roles`, `role_divisions` | Referenced but usage commented out |

---

## 5. `statistics` Table — Full Column Set (Inferred from Code)

| Column | Type | Description |
|--------|------|-------------|
| `id` | int PK | Auto-increment |
| `unique_id` | string | FreeSWITCH call UUID (the join key with Sofia) |
| `consultant_id` | int FK | Linked consultant |
| `credit_customer_id` | int FK | Linked credit customer record |
| `user_id` | int FK | User account |
| `group_id` | int | Tenant/group |
| `site_ivr_number_id` | int FK | The DID that was called |
| `provider_id` | int | 1=OVH/AWS |
| `type_id` | int | Service type (1-4) |
| `type` | string | e.g., 'call' |
| `src_number` | string | Caller's E.164 number |
| `dst_number` | string | Coach's actual phone number |
| `extension` | int | 4-digit extension (0 if N/A) |
| `start_time` | datetime | When call arrived |
| `ringing_start_time` | datetime | When outbound ring started |
| `connected_time` | datetime | When coach/SD answered |
| `hangup_time` | datetime | When destination hung up |
| `end_time` | datetime | When caller hung up |
| `total_duration` | int | Seconds from start to end |
| `conversation_duration` | int | Seconds from connected to hangup |
| `credit_before` | decimal | Credit balance before this block |
| `credit_after` | decimal | Credit balance after this block |
| `coach_rate` | decimal | Per-minute rate for coach |
| `vat_rate` | decimal | Effective VAT % |
| `consultant_earning_for_minute` | decimal | Coach commission/minute |
| `consultant_total_earning` | decimal | Final coach earnings for call |
| `credit_without_vat` | decimal | Net credit used excl. VAT |
| `surcharge_amount` | decimal | Number-based surcharge |
| `allocated_vat_amount` | decimal | VAT portion of charge |
| `customer_currency_code` | string | Customer billing currency |
| `customer_currency_rate` | decimal | FX rate customer→base |
| `coach_currency_code` | string | Coach payment currency |
| `coach_currency_rate` | decimal | FX rate coach→base |
| `company_and_coach_currency_rate` | decimal | Coach currency → EUR |
| `company_and_customer_currency_rate` | decimal | Customer currency → EUR |
| `status` | string | NORMAL/SHORT CALL/NO ANSWER/DISCONNECT/REMOTE BUSY/LOCAL_BUSY/EXTERNAL_BUSY/CUSTOMER_HANGUP_BEFORE_ANSWER |
| `created_at` | timestamp | Laravel auto |
| `updated_at` | timestamp | Laravel auto |

---

## 6. Business Rules Implemented in Sentinel

### Credit Calculation
```
avb_time (seconds) = current_credits / (call_rate * (vat_rate + 100) * fx_rate / 100 / 60)
slot_value = min(avb_time, 300)   # max 5-minute blocks
minimum_gate = avb_time > 30     # call_bench_mark_time
```

### Rate Formula
```
effective_rate_per_minute = call_rate * (1 + vat_rate/100) * fx_rate
effective_rate_per_second = effective_rate_per_minute / 60
credit_amount = rate_per_second * slot_seconds
```

### Hangup Billing Reconciliation
1. Compute `conversation_duration = connected_time → hangup_time`
2. Look up first tracing with status=`customer_authenticated` (initial block deduction)
3. Look up latest tracing (most recent block)
4. Compute `actual_used = rate_per_second * iActualTime` (time since last block update)
5. `refund_or_charge = pre_deducted - actual_used`
6. Update `credits_customers.current_credits += refund_or_charge`
7. Set final statistics fields

### Short Call Handling
If `conversation_duration ≤ 30s`: refund the entire pre-deducted amount. Status = "SHORT CALL".

### Call Summary Status Codes
| Code | Perl variable | Label |
|------|---------------|-------|
| 1 | `call_summary=1` | NORMAL |
| 2 | `call_summary=2` | SHORT CALL |
| 3 | `call_summary=3` | NO ANSWER |
| 4 | `call_summary=4` | DISCONNECT |
| 5 | `call_summary=5` | REMOTE BUSY (consultant busy) |
| 6 | `call_summary=6` | LOCAL_BUSY (Voxbone channel exceeded) |
| 7 | `call_summary=7` | EXTERNAL_BUSY (coach rejected/busy) |
| 8 | `call_summary=1` but no connected_time | CUSTOMER_HANGUP_BEFORE_ANSWER |

### Consultant Status Lifecycle
- Set to `2` (busy) when connected_time event received
- Set to `1` (online) when hangup billing completes successfully
- **Risk:** If billing fails, consultant stays stuck in busy state

---

## 7. Cron Jobs / Background Tasks

No Laravel scheduled tasks found in Sentinel itself. The `recordingMgt.pl` Perl script is run externally (presumed cron on the FreeSWITCH server):
- Uploads yesterday's recordings from local FS to S3
- Reduces SD recording bit-rate (8kbps mono via lame)
- Deletes empty recording directories

No other background tasks identified in the Sentinel codebase.

---

## 8. Validators (Input Validation)

All inputs validated before processing. Validator classes enforce:
- Required fields present
- `timestamp` format: `Y-m-d H:i:s`
- `call_status` is a valid integer
- Phone numbers are numeric strings

No SQL injection protection beyond Laravel Eloquent ORM (parameterized queries used throughout). No input length validation observed.

---

## 9. Known Gaps & Assumptions

- `BaseRepository::updateCreditCustomer()` is a **read-modify-write** inside `DB::beginTransaction()`. This prevents corruption within a single request but does NOT prevent race conditions between two concurrent API requests for the same credit customer. Two simultaneous calls could both read the same balance and both be authorized.
- Consultant status toggle is a simple boolean flip — no race condition guard. Two concurrent toggle calls could result in indeterminate state.
- `calculateEarningsAndCallTime()` uses `DB::table('statistics')` with `whereBetween(created_at, ...)` — no index hint. On large tables, this could be slow and block the call setup while coach is authenticating.
- No TTL/expiry on `ivr_known_users` records — a customer's pre-auth persists indefinitely. If customer changes their PIN, the old mapping still exists.
- `serviceDeskRepository::active()` queries for type=3 AND language_id — if no SD agent has that language, returns empty array. Sofia then plays "not available" and records a voice message. The voice message file is saved locally on the FreeSWITCH server but no API endpoint is called to notify the SD team.
