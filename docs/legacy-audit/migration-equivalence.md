# Migration Equivalence Table
## Phase 0 → Phase 2/3/4 — ifonix VoIP Platform Modernization
**Audited:** 2026-05-17

This table maps every legacy component to its modern equivalent. It is the authoritative cross-reference for Phases 2–4 implementation.

---

## Sofia (Perl) → Lua Dialplan

| Legacy | File | Modern Equivalent | Change Notes |
|--------|------|-------------------|-------------|
| `checkServiceType.pl` (main entry) | sofia/bin | Lua dialplan script: `dialplan/route.lua` | Lua runs natively in FS, no mod_perl overhead. Keep same session variable names for continuity. |
| `authentication.pl` → `doAuth()` | sofia/bin | Lua: `auth/pin_auth.lua` | Replace `goto` with Lua `while` loops. Lua natively supports `session:playAndGetDigits()`. |
| `authentication.pl` → `saveMyPin()` | sofia/bin | Lua: `auth/pin_auth.lua` → POST `/v1/auth/save-known-user` | Currently a no-op (commented out). Implement fully in new system. |
| `authentication.pl` → `askServiceDesk()` | sofia/bin | Lua: `util/service_desk_prompt.lua` | Shared helper, called from multiple scripts. |
| `dialCoach.pl` | sofia/bin | Lua: `dialplan/dial_coach.lua` | Replace 3 pre-generated UUIDs (only 1 used) with 1. Keep `{RECORD_STEREO=false,RECORD_READ_ONLY=true}` dial string flags. |
| `coachSessionHandler.pl` | sofia/bin | Lua: `billing/time_block_manager.lua` (background via `api:execute('luarun', ...)`) | Critical: add strict timeout on `/v1/billing/tick` HTTP call (≤2000ms, per R-SIP-01). Fire at t=-5s before block end, not after. |
| `playAvailableTime.pl` | sofia/bin | Lua: `ivr/play_available_time.lua` | Trivial conversion. Same `saySecToTime()` logic. |
| `coachMenu.pl` | sofia/bin | Lua: `ivr/coach_menu.lua` | Replace `goto MENUBEGIN` with Lua loop. |
| `serviceDesk.pl` | sofia/bin | Lua: `dialplan/service_desk.lua` | Multi-number failover loop: iterate list from `/v1/service-desk/active`. |
| `servicedeskSessionHandler.pl` | sofia/bin | Lua: `billing/sd_session_handler.lua` | Simpler than coachSessionHandler — no time-blocks. Just wait for answer, post connected event. |
| `extension.pl` | sofia/bin | Lua: `auth/extension_auth.lua` | Extension dial (4-digit code). Same logic, cleaner in Lua. |
| `premium.pl` | sofia/bin | **Deferred** | Currently empty. Document as gap. Implement when business logic defined. |
| `recordingMgt.pl` | sofia/bin | Python worker: `workers/recording_uploader.py` | Replace Perl batch with Python + boto3. Use S3 SDK, not Amazon::S3 Perl. Move to scheduled Lambda or ECS cron task. |
| `FsCommon.pm` → `fsPrint()` | sofia/lib | Lua: `util/logger.lua` using `freeswitch.consoleLog()` | Use structured JSON log format (R-LOG). Include `call_uuid`, `account_id`, `event_type`, etc. |
| `FsCommon.pm` → `saySecToTime()` | sofia/lib | Lua: `ivr/say_time.lua` | Port file_string assembly pattern. Same `uuid_displace` approach works in Lua. |
| `FsCommon.pm` → `leaveAMsg()` | sofia/lib | Lua: `ivr/leave_message.lua` | Add API call to notify SD team after recording. Currently missing. |
| `FsCommon.pm` → `playCurrency()` | sofia/lib | Lua: `ivr/play_number.lua` | Port digit sound playback. |
| `utils.pm` → `getConfValue()` | sofia/lib | Lua: `util/config.lua` using `freeswitch.API:execute('global_getvar', ...)` or Lua config table | Load from environment / AWS SSM at startup, not from flat INI file. |
| `utils.pm` → `microSecToDateTime()` | sofia/lib | Lua: `util/time.lua` | Use `os.date()` + microsecond math. |
| `utils.pm` → `getRecDir()` | sofia/lib | Lua: `util/recording.lua` | `os.date('%Y%m%d')`. |
| `ApiClient.pm` → `postApi()` | sofia/lib | Lua: `api/client.lua` using `luasocket` + `lua-http` or `freeswitch.API curl` | **Add strict 2000ms timeout** (R-SIP-01). Add request_id header. Add retry logic ONLY for non-call-path requests. |
| `AwsUploader.pm` | sofia/lib | Python: `workers/recording_uploader.py` using `boto3` | No Perl Amazon::S3. Use IAM role instead of access keys in config. |
| `MongoDbConnection.pm` | sofia/lib | **Replaced by CloudWatch** | Structured JSON logs → CloudWatch. No MongoDB. |
| `sofia.conf` | sofia/lib | AWS SSM Parameter Store + Secrets Manager | Host URLs → SSM. Tokens/keys → Secrets Manager. Remove flat config file. |

---

## Sentinel (PHP) → FastAPI (Python)

| Legacy Endpoint | PHP File | Modern Endpoint | FastAPI File | Change Notes |
|-----------------|----------|-----------------|-------------|-------------|
| `POST /api/v1/call/validate` | `CallValidateController@checkValidity` | `POST /v1/call/authorize` | `routers/call.py` | Combine validate + pre-auth into single authorize call. Return same fields. Add Redis credit lookup before DB. |
| `POST /api/v1/customer/call/confirm` | `CustomerAuthController@checkValidity` | `POST /v1/auth/customer/confirm` | `routers/auth.py` | Same logic. Use Redis for credit balance. Use atomic DECRBY (R-BILL-01). |
| `POST /api/v1/coach/call/confirm` | `CoachAuthController@checkValidity` | `POST /v1/auth/coach/confirm` | `routers/auth.py` | Same. |
| `POST /api/v1/customer/auth` | `CustomerAuthController@store` | `POST /v1/auth/save-known-user` | `routers/auth.py` | Save phone→user mapping. |
| `POST /api/v1/coach/auth` | `CoachAuthController@store` | `POST /v1/auth/save-known-user` | `routers/auth.py` | Same endpoint, mode param. |
| `POST /api/v1/check/call/time` | `CustomerActionsController@fetch` | `POST /v1/billing/tick` | `routers/billing.py` | **Critical.** Must use atomic Redis DECRBY (R-BILL-01). Return new block. |
| `POST /api/v1/call/status` | `CustomerActionsController@store` | `POST /v1/call/status` | `routers/call.py` | Lifecycle events. Write to Redis first, async persist to DB. |
| `POST /api/v1/service-desk/call/status` | `ServiceDeskController@store` | `POST /v1/service-desk/status` | `routers/service_desk.py` | Same. |
| `POST /api/v1/service-desk/active/list` | `ServiceDeskController@list` | `GET /v1/service-desk/active` | `routers/service_desk.py` | Change to GET (idempotent). Cache result in Redis (15s TTL). |
| `POST /api/v1/check/customer/credits` | `CustomerActionsController@get` | `GET /v1/customer/{credit_code}/credits` | `routers/customer.py` | Read from Redis first. |
| `POST /api/v1/toggle/coach/status` | `CoachActionsController@toggle` | `POST /v1/coach/{coach_id}/toggle-status` | `routers/coach.py` | Use Redis distributed lock to prevent concurrent toggles. |
| `POST /api/v1/coach/extension` | `CallValidateController@checkCoachExtension` | `POST /v1/auth/extension/confirm` | `routers/auth.py` | Extension dial auth. Same logic. |

---

## Business Logic Functions

| Legacy Function | Location | Modern Equivalent | Notes |
|----------------|----------|-------------------|-------|
| `CallValidateRepository::validateDestination()` | Sentinel | `services/call_router.py::validate_destination()` | Determine service type from DID |
| `CallValidateRepository::handleCreditStatus()` | Sentinel | `services/credit.py::calculate_time_block()` | `avb_time = credits / rate_per_sec`, `slot = min(avb_time, 300)` |
| `CallValidateRepository::valuesInitialize()` | Sentinel | `services/credit.py::build_session_context()` | Assemble credit + rate context |
| `BaseRepository::callRateWithVat()` | Sentinel | `services/billing.py::effective_rate()` | `rate * (1 + vat/100) * fx_rate` |
| `BaseRepository::vatPerSecond()` | Sentinel | `services/billing.py::vat_per_second()` | |
| `CustomerActionsRepository::timeExceedMinimumRange()` | Sentinel | `services/billing.py::finalize_bill()` | Full hangup reconciliation |
| `CustomerActionsRepository::timeNotExceedMinimumRange()` | Sentinel | `services/billing.py::refund_short_call()` | Short call refund |
| `BaseRepository::consultantValidate()` | Sentinel | `services/auth.py::validate_consultant()` | Not blocked, not deleted, user active |
| `BaseRepository::customerValidate()` | Sentinel | `services/auth.py::validate_customer()` | Same checks for credit customer |
| `BaseRepository::saveIvrStatistics()` | Sentinel | `repositories/cdr.py::upsert_statistics()` | Write CDR to Redis first, async to Postgres |
| `BaseRepository::saveIvrTracing()` | Sentinel | `repositories/cdr.py::append_tracing()` | **Append-only, never update** (R-BILL-02) |
| `BaseRepository::updateCreditCustomer()` | Sentinel | `services/credit.py::atomic_deduct()` | **Replace with Redis DECRBY Lua script** (R-BILL-01) |
| `BaseRepository::ivrStatisticsCreditUpdate()` | Sentinel | `repositories/cdr.py::update_statistics_credit()` | Part of hangup reconciliation |
| `BaseRepository::updateConsultantStatus()` | Sentinel | `services/consultant.py::set_status()` | Write to DB + Redis cache |
| `BaseRepository::calculateEarningsAndCallTime()` | Sentinel | `services/consultant.py::get_earnings()` | DB aggregation, cache in Redis (5min TTL) |
| `BaseRepository::handleIvrCallSummaryStatus()` | Sentinel | `services/billing.py::classify_call()` | Returns NORMAL/SHORT CALL/etc. |
| `CustomerActionsRepository::availableCallBlock()` | Sentinel | `services/billing.py::get_next_block()` | Time-block renewal |
| `CoachAuthRepository::calculateEarningsAndCallTime()` | Sentinel | `services/consultant.py::get_earnings()` | Same as above, shared |
| `ServiceDeskRepository::active()` | Sentinel | `services/service_desk.py::get_active_agents()` | Return list of online SD agents |

---

## Auth Mechanism

| Legacy | Modern |
|--------|--------|
| Laravel Passport OAuth2 client credentials (JWT) stored in flat config file | JWT issued by FastAPI auth service, validated via shared secret or RS256 key from AWS Secrets Manager |
| Single static token per FreeSWITCH instance | Per-request signed JWT with short TTL, or mutual TLS between FreeSWITCH and FastAPI |
| Token expired (Jan 2022) — system running on expired token | Implement automatic token refresh in Lua client |
| Bearer token in Authorization header (preserve) | Same header, same field name |

---

## CDR / Recording Pipeline

| Legacy | Modern |
|--------|--------|
| `statistics` written synchronously in PHP during live call | Write CDR stub to Redis at call start, persist to PostgreSQL async via billing worker (R-BILL-03) |
| `tracings` written synchronously per event | Write to Redis queue, billing worker persists in order |
| Recording stored locally on FS server, uploaded by nightly Perl cron | Streaming upload to S3 via `execute_on_answer=record_session` + post-call Lambda trigger |
| S3 credentials in `sofia.conf` plaintext | IAM instance role on FreeSWITCH EC2 (no credentials at all) |
| No notification when voicemail left | POST to FastAPI `/v1/service-desk/voicemail` after recording |

---

## Configuration & Secrets

| Legacy Item | Modern Location |
|-------------|----------------|
| `sofia.conf` → Sentinel host | AWS SSM: `/ifonix/sentinel/host` |
| `sofia.conf` → API bearer token | AWS Secrets Manager: `ifonix/sofia/api-token` |
| `sofia.conf` → AWS access key | IAM instance role (no key needed) |
| `sofia.conf` → MongoDB config | Deleted (replaced by CloudWatch) |
| `sofia.conf` → provider names | AWS SSM: `/ifonix/providers/primary` |
| `sofia.conf` → sound paths | Keep in FreeSWITCH config, not in Lua config |
| `sofia.conf` → tuning params (attempts, timeouts) | AWS SSM: `/ifonix/ivr/config` |

---

## Gaps & Deferred Items

| Item | Current State | Action Needed |
|------|--------------|---------------|
| `premium.pl` | Empty — no business logic | Define requirements before implementing |
| Remember-me PIN save | Commented out in `authentication.pl` | Decide: implement or remove permanently |
| Voice message notification | SD voicemail recorded locally, no API call | Add POST to notification endpoint in `leaveAMsg()` equivalent |
| Multi-language support (es) | es defined in language array but no sound path | Add Spanish sound files or remove from routing |
| Provider failover | provider2 and provider3 commented out | Define multi-provider strategy for new system |
| FX rate provider | Populated by main Helios app (not Sentinel) | Identify the Helios scheduler, migrate or replace |
| MongoDB logs | Disabled (commented out) | Confirm no production dependency; remove |
| Direct dial pre-auth `ivr_known_users` | Persistent, no TTL | Migrate table; define expiry policy |
