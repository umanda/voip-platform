# Risk Findings — Legacy ifonix VoIP Platform
## Phase 0 — Security, Integrity, and Performance Audit
**Audited:** 2026-05-17

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 6     |
| HIGH     | 5     |
| MEDIUM   | 6     |
| LOW      | 4     |

---

## CRITICAL Findings

---

### CRIT-01: AWS IAM Credentials Hardcoded in Version-Controlled Config

**File:** `sofia/lib/sofia.conf`
**Lines:**
```ini
[aws]
aws_access_key_id = REDACTED_AWS_ACCESS_KEY
aws_secret_access_key = REDACTED_AWS_SECRET_KEY
recording_bucket = webgroup-sofia-recordings
```

**Risk:** Any person with read access to the Sofia repository has live AWS credentials. If these keys have `s3:*` or broader permissions, they could be used to exfiltrate all call recordings, or to access other AWS services in the account. If the repo has ever been pushed to a remote (GitHub, GitLab, Bitbucket), the credentials are likely already in git history even if removed now.

**Action Required:**
1. **Immediately rotate** the IAM access key `REDACTED_AWS_ACCESS_KEY`.
2. Audit AWS CloudTrail for any access using this key from unexpected IPs.
3. Replace with an IAM instance role attached to the FreeSWITCH EC2 — no credentials in config at all.
4. Run `git log --all -p sofia/lib/sofia.conf | grep -E 'AKIA|secret'` to confirm git history exposure.

---

### CRIT-02: RSA Private Key Committed to Repository

**File:** `sofia/lib/id_rsa`

**Risk:** This private key is used in `sentinel_key_gen.sh` to SSH into `10.0.0.20` and regenerate OAuth tokens. Any holder of this key can authenticate as the Sofia system user on that host. If the host runs other services (Sentinel, the main Helios app, the database), lateral movement is possible with a single file.

**Action Required:**
1. **Immediately revoke** this key from `~/.ssh/authorized_keys` on `10.0.0.20`.
2. Generate a new key pair; store the private key in AWS Secrets Manager, not in the filesystem.
3. The token renewal process should use an IAM-authenticated mechanism (SSM Run Command, Lambda) instead of SSH.

---

### CRIT-03: OAuth Client Secret Committed to Repository (Two Locations)

**File 1:** `sofia/lib/sentinel_key_gen.sh`
```bash
# Contains: client_secret=REDACTED_OAUTH_SECRET_1
```

**File 2:** `sofia/lib/new_key.txt` (plaintext)
```
Client ID: 4
Client secret: REDACTED_OAUTH_SECRET_2
```

**Risk:** Any holder of a valid OAuth client secret can generate new Bearer tokens for Sentinel and call any IVR API endpoint, including `/check/call/time` (to steal credit time-blocks) and `/call/status` (to forge call records).

**Action Required:**
1. Rotate both OAuth client secrets in Sentinel (invalidating all existing tokens derived from them).
2. Delete `new_key.txt` immediately; remove from git history.
3. Store client secrets in AWS Secrets Manager; load at runtime only.

---

### CRIT-04: Bearer Token Expired Since January 2022

**File:** `sofia/lib/sofia.conf` (`key = eyJ0eXAiOiJKV1Qi...`)

**Risk:** The JWT in `sofia.conf` has `exp: 1641878369` (January 11, 2022). The system has operated for 4+ years on an expired token. This means either:
- Sentinel's `['client']` middleware does not validate token expiry (a security defect in Sentinel); or
- The token was renewed and `sofia.conf` was not updated for the audit snapshot.

If Sentinel is not validating expiry, any expired (or even crafted) token will be accepted. This removes authentication entirely as a security control.

**Action Required:**
1. Check Laravel Passport middleware — confirm whether `CheckClientCredentials` validates the `exp` claim.
2. If it does not, patch Sentinel to reject expired tokens.
3. Regardless: rotate the token immediately and configure automatic renewal (target: 24h TTL with auto-refresh in Lua client).

---

### CRIT-05: No HTTP Timeout on Sentinel API Calls (Live Call Blocking)

**File:** `sofia/lib/ApiClient.pm`
```perl
my $client = REST::Client->new();  # no timeout
$client->setHost($apiUrl);
```

**Risk:** If Sentinel becomes slow or unreachable, the `REST::Client` call blocks indefinitely. The FreeSWITCH Perl thread is held. Since `mod_perl` runs dialplan scripts in-process, a hung call will hold a FreeSWITCH worker thread indefinitely. Under sustained Sentinel latency, all available threads could be exhausted, causing new incoming calls to queue until they time out at the SIP layer — effectively a self-induced denial of service.

A blocked call during `coachSessionHandler.pl`'s `/check/call/time` loop will also cause the `sched_hangup` to fire while the billing reconciliation is still pending, potentially resulting in credit deducted but not reconciled.

**Violates:** R-SIP-01 (2000ms max on any HTTP call in call path).

**Action Required:** All Lua replacements must use `settimeout(2)` on luasocket. The immediate workaround in Perl is `REST::Client->new({timeout => 2})`.

**Status:** Mitigated in new system
**Current behavior:** Sofia waits indefinitely — caller hears silence if Sentinel is slow/down
**New behavior:** Lua enforces 2000ms hard timeout → plays error IVR → clean hangup
**Implemented in:** freeswitch/lua/lib/http.lua (http.TIMEOUT = 2)
**Rule reference:** telecom-rules.md R-FLOW-01

---

### CRIT-06: MongoDB Password in Config (`admin123`)

**File:** `sofia/lib/sofia.conf`
```ini
[mongodb]
password = admin123
```

**Risk:** Even though all MongoDB `addLog()` calls are commented out in production, the password `admin123` is a default/trivial credential that may protect a MongoDB instance accessible from the network. If other applications use the same MongoDB instance, this credential gives full access. The fact that logging is disabled does not mean the MongoDB instance is unused or unreachable.

**Action Required:**
1. Rotate the MongoDB password immediately.
2. Confirm whether the MongoDB instance is reachable from the internet or only from VPC.
3. Verify no other applications depend on this MongoDB instance before decommissioning.

---

## HIGH Findings

---

### HIGH-01: Race Condition in Credit Deduction (Concurrent Calls)

**File:** `Sentinel/Repositories/BaseRepository.php` → `updateCreditCustomer()`
**File:** `Sentinel/Repositories/Ivr/CustomerActionsRepository.php` → `availableCallBlock()`

```php
DB::beginTransaction();
$customer = CreditsCustomer::where('id', $creditId)->lockForUpdate()->first();
$currentCredits = $customer->current_credits;
$newCredits = $currentCredits - $deductAmount;
CreditsCustomer::where('id', $creditId)->update(['current_credits' => $newCredits]);
DB::commit();
```

**Risk:** `lockForUpdate()` within a transaction prevents two concurrent PHP workers from reading the same row simultaneously within PostgreSQL. However:
- If a customer initiates two calls nearly simultaneously (e.g., double-tapping a callback button), the second `/call/validate` request may arrive before the first has deducted credit. Both will read the same `current_credits` value and both will be authorized, resulting in double the credit being committed.
- The transaction protects against corruption within a single request but not against the TOCTOU (time-of-check-to-time-of-use) window between the `/call/validate` check and the `/customer/call/confirm` deduction.

**Violates:** R-BILL-01 (atomic credit operations).

**Migration Fix:** Redis `DECRBY` with a Lua script that atomically checks balance and deducts. The Postgres row is the source of truth for settlement, not for gating.

---

### HIGH-02: Credit Loss if Sentinel Unreachable During Active Call

**File:** `sofia/bin/coachSessionHandler.pl`

```perl
my $avbTimeJson = postApi("/api/v1/check/call/time", $data);
my $avb_time = $avbTimeJson->{avb_time};
if (!$avb_time || $avb_time < 0) {
    # ...
    $session->setVariable("ran_out_credits", "1");
    $api->execute("uuid_kill", $uuid);
}
```

**Risk:** If `/check/call/time` returns an empty or error response (network blip, Sentinel restart), `$avb_time` will be undef/falsy. The code treats this identically to genuine credit exhaustion and kills the call. The customer loses their remaining pre-paid credit without consuming it.

Additionally, with no HTTP timeout on `REST::Client`, a slow Sentinel response causes the 5-second pre-renewal window to be consumed by waiting, causing `sched_hangup` to fire before the renewal completes — killing the call regardless of the Sentinel response.

**Violates:** R-FLOW-01 (auth timeout → route to error IVR, not silent drop), R-BILL-03 (CDRs must not be lost).

**Migration Fix:** In Lua, explicitly distinguish HTTP timeout from zero-credit response. On timeout: extend the block 60s and log an alert. On zero credit: gracefully announce and disconnect.

---

### HIGH-03: Caller Credit Deducted but Not Reconciled on Billing Worker Failure

**File:** `sofia/bin/checkServiceType.pl` (hangup handler)
```perl
sub hangUpFunction {
    # POST /api/v1/call/status — synchronous HTTP in hangup handler
    my $saveStats = postApi("/api/v1/call/status", $data);
}
```

**Risk:** The hangup CDR write is a synchronous HTTP call to Sentinel inside the FreeSWITCH hangup handler. If Sentinel is unreachable at this moment, the CDR is lost entirely — no retry, no queue, no dead-letter store. The credit block deducted at call start is never reconciled, meaning the customer overpays for the call permanently.

**Violates:** R-BILL-02 (CDRs append-only), R-BILL-03 (CDRs never lost).

**Migration Fix:** Billing worker subscribes to FreeSWITCH ESL `CHANNEL_HANGUP_COMPLETE` events. CDR written to Redis at call start (stub), enriched through lifecycle, finalized at hangup. Postgres persistence is async via the worker; Redis is the intermediate store that survives Sentinel restarts.

---

### HIGH-04: Consultant Stuck in BUSY State if Billing Call Fails

**File:** `sofia/bin/coachSessionHandler.pl`
```perl
# After call ends, no code resets consultant ivr_status
```
**File:** `Sentinel/Repositories/BaseRepository.php` → `updateConsultantStatus()`

**Risk:** If the hangup status POST to `/call/status` fails (network error, Sentinel crash), the consultant's `ivr_status` in the DB remains `2` (BUSY) indefinitely. The consultant appears unavailable for all future calls until they manually toggle their status back to ONLINE or until someone fixes it in the DB. In a production system, this could silently reduce call capacity.

**Action Required:** The new billing worker must guarantee status reset on call termination, with a dead-letter retry mechanism. Additionally, a reconciliation job should scan for consultants who have been BUSY for more than 2× the max call block time with no active call record.

---

### HIGH-05: Service-Desk Voicemail Recorded with No Notification

**File:** `sofia/lib/FsCommon.pm` → `leaveAMsg()`

```perl
sub leaveAMsg {
    $session->execute("record", "$recDir/sd/$unique_id.mp3,180,300");
    # No API call after recording completes
    # No notification to service desk team
}
```

**Risk:** When a caller reaches voicemail (all SD agents unavailable), the recording is saved to local disk but no notification is sent to the SD team. The SD team has no way to know a voicemail exists except by checking the filesystem. In production, voicemails are effectively silent — callers who cannot reach support get no callback.

**Migration Fix:** After `session:recordFile()` in Lua, POST to `/v1/service-desk/voicemail` with the recording path/S3 key and caller details.

---

## MEDIUM Findings

---

### MED-01: `goto` Control Flow Makes Code Untestable and Fragile

**File:** `sofia/bin/authentication.pl`
```perl
ATTEMPT: {
    # ...
    goto ATTEMPT if ($attempts < 3);
}
```

**Risk:** Perl `goto` jumps bypass normal stack frames and make automated testing nearly impossible. A logic error in the attempt counter can create an infinite loop in the dialplan thread. This is a maintenance and reliability risk, not a security risk.

**Migration Fix:** Lua `while` loop with explicit attempt counter. No `goto`.

---

### MED-02: All Three Providers Configured to the Same Gateway

**File:** `sofia/bin/dialCoach.pl`
```perl
my $provider1 = getConfValue("providers", "provider1"); # = "voxbone-outbound"
my $provider2 = getConfValue("providers", "provider2"); # = "voxbone-outbound"
my $provider3 = getConfValue("providers", "provider3"); # = "voxbone-outbound"
# Only $provider1 is used; $provider2 and $provider3 are in commented-out dial strings
```

**Risk:** Provider failover logic exists in the code but is disabled. If `voxbone-outbound` becomes unavailable, there is no fallback. All outbound calls fail simultaneously with no automatic recovery. The commented-out code suggests this was intentional but the failover strategy was never defined.

**Action Required:** Define multi-provider strategy (see Gaps in `migration-equivalence.md`).

---

### MED-03: Phone Number Normalization Is Incomplete

**File:** `sofia/bin/checkServiceType.pl`
```perl
$src =~ s/\+//;  # strip leading +
```

**Risk:** Caller IDs from different SIP providers may arrive in different formats:
- `+31612345678` (E.164 with +)
- `0031612345678` (country code with 00)
- `31612345678` (bare)
- `0612345678` (local format)

Only the `+` is stripped. No normalization to E.164. This affects `ivr_known_users` lookups for direct-dial pre-auth — if a returning user calls from the same number but the carrier sends a different format, they will not be recognized and will be forced to re-enter their PIN.

**Migration Fix:** Normalize to E.164 in the Lua dialplan using a proper normalization function before any number-based lookup.

---

### MED-04: `provider_id` Hardcoded to `1` in Session Variable

**File:** `sofia/bin/checkServiceType.pl`
```perl
$session->setVariable('provider_id', '1');
```

**Risk:** All CDRs record `provider_id = 1` regardless of which SIP gateway actually handled the call. If multiple providers are enabled in the future, billing and reporting will be incorrect. This also breaks any provider-level analytics.

**Migration Fix:** Determine `provider_id` from the actual gateway used in the origination string; set it dynamically after bridge.

---

### MED-05: Unstructured and Partially Disabled Logging

**File:** `sofia/lib/FsCommon.pm` → `fsPrint()` and `MongoDbConnection.pm`

```perl
sub fsPrint {
    freeswitch::consoleLog("INFO", "[$scriptName] $message\n");
    # addLog() commented out — MongoDB logging disabled
}
```

**Risk:** All production logging is flat-text console logs with no structured fields. There is no `call_uuid`, `account_id`, `event_type`, or other machine-parseable fields. Debugging a billing dispute requires grepping through FreeSWITCH console logs by timestamp. The MongoDB structured logging was implemented but disabled — it is unclear whether this was intentional or due to a bug.

**Migration Fix:** Lua `util/logger.lua` must emit structured JSON logs with required fields (R-LOG) to stdout → CloudWatch.

---

### MED-06: `system()` Calls in Recording Script (Command Injection Surface)

**File:** `sofia/bin/recordingMgt.pl`
```perl
system("aws s3 cp $localPath s3://$bucket/$s3Key");
```

**Risk:** If `$localPath` or `$s3Key` are derived from external input (e.g., the FreeSWITCH call UUID or caller ID), and if those values contain shell metacharacters, this creates a command injection vulnerability. Even if current values are safe, the pattern is fragile. The `system()` call also does not check the return code, so failed uploads are silent.

**Migration Fix:** Use `boto3` in `workers/recording_uploader.py` — no shell execution, explicit error handling, IAM role authentication.

---

## LOW Findings

---

### LOW-01: `premium.pl` Is Empty — Service Type 5 Has No Handler

**File:** `sofia/bin/premium.pl`

**Risk:** Calls to DIDs with `type_id = 5` will reach the `when(5)` branch in `checkServiceType.pl`, which runs `premium.pl`, which does nothing (or throws an unhandled error). These callers will hear silence and the call will drop. If any active DID in production has `type_id = 5`, this is a live issue.

**Action Required:** Audit `site_ivr_numbers` for any rows with `type_id = 5`. If none, the risk is theoretical. If any exist, route them to an error IVR until business logic is defined.

---

### LOW-02: Remember-Me PIN Save Feature Disabled

**File:** `sofia/bin/authentication.pl` → `saveMyPin()`
```perl
sub saveMyPin {
    # TODO: implement
    # postApi("/api/v1/customer/auth", ...) — commented out
}
```

**Risk:** The feature is disabled — not a security risk. However, since `saveMyPin()` is called in the code path but does nothing, the IVR may present a "save your number" prompt to users that has no effect. This is a silent UX failure that erodes user trust.

**Action Required:** Either implement the feature fully or remove the prompt from the IVR flow.

---

### LOW-03: Debug Artifact in API Client

**File:** `sofia/lib/ApiClient.pm`
```perl
if (!$responseContent) {
    system("ssh -h");  # debug artifact — prints SSH help text to console
}
```

**Risk:** Not a functional risk — `ssh -h` is harmless. However, it produces unexpected output in FreeSWITCH console logs on every empty API response, polluting logs and making real errors harder to find.

**Action Required:** Remove before migration.

---

### LOW-04: Three Outbound UUIDs Generated but Only One Used

**File:** `sofia/bin/dialCoach.pl`
```perl
my $uuid1 = $api->execute("create_uuid");
my $uuid2 = $api->execute("create_uuid");  # unused
my $uuid3 = $api->execute("create_uuid");  # unused
```

**Risk:** Minor resource waste and code confusion. The `create_uuid` ESL command is cheap, but the dead variables suggest the code was partially refactored and the cleanup was not completed.

**Action Required:** Generate one UUID in Lua replacement.

---

## Completion Check Answers

As required by the audit prompt (`01-analyze-legacy.md`):

| Question | Answer |
|----------|--------|
| What is the exact JSON/form body Sofia sends to Sentinel? | Form-encoded POST: `unique_id`, `src_number`, `dst_number`, `provider_id`, `start_time` (plus endpoint-specific fields). Auth via `Authorization: Bearer <token>` header. |
| What does Sentinel return when credit is insufficient? | `{api: {}, body: {avb_time: 0, slot_value: 0, ...}, message: {}}`. Sofia checks `avb_time < call_bench_mark_time` (30s) and plays insufficient-credit prompt. |
| How long does Sofia wait before timing out on the API? | **Indefinitely.** `REST::Client->new()` with no timeout parameter. This is CRIT-05. |
| What FreeSWITCH actions does Sofia take after successful auth? | Sets session variables (credit context), spawns `coachSessionHandler.pl` via `perlrun` in background, then bridges call via `dialCoach.pl`. |
| Where are CDRs written and what fields do they contain? | Written synchronously to PostgreSQL `statistics` table via Sentinel `/call/status` endpoint. ~30 columns (see `schema-map.md` §3). Also `tracings` append-only per event. |
| What happens if Sentinel is unreachable during a live call? | `/check/call/time` returns empty → call killed, credit lost. Hangup POST fails → CDR lost, consultant stuck BUSY. No retry, no queue. (HIGH-01, HIGH-02, HIGH-03) |

---

## Risk Prioritization for Migration

| Priority | Findings | Action |
|----------|----------|--------|
| Before any code is written | CRIT-01, CRIT-02, CRIT-03, CRIT-04, CRIT-06 | Rotate all credentials immediately |
| Phase 1 (scaffold) | CRIT-05, HIGH-01 | Implement timeouts and atomic credit ops from day one |
| Phase 2 (auth + credit) | HIGH-02, HIGH-03 | Redis-backed credit gating, async CDR pipeline |
| Phase 3 (Lua dialplan) | HIGH-04, MED-03, MED-04 | Consultant status recovery, number normalization |
| Phase 4 (billing worker) | HIGH-05, MED-05 | Voicemail notification, structured logging |
| Pre-cutover | MED-01, MED-02, MED-06 | Provider failover, remove system() calls |
| Post-cutover cleanup | LOW-01 through LOW-04 | Remove dead code, implement or remove premium.pl |
