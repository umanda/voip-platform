# Database Schema Map — galaxy_2
## Phase 0 — ifonix VoIP Platform Modernization
**Audited:** 2026-05-17

---

## Connection Attempt

Direct DB connection was not available during this audit (requires password for `dev_ifx` on localhost:5432). Schema is reverse-engineered entirely from:
- Laravel Eloquent models in the Sentinel codebase
- Column names referenced in repository `save*` and `update*` methods
- `BaseRepository::transformStatisticsData()` and `saveIvrStatistics()` (most complete column map)
- `transformTracingData()` and `saveIvrTracing()`

> **ACTION REQUIRED:** Run the SQL queries in Section 8 against the live DB to validate all inferred schemas and discover tables not yet identified.

---

## 2. Table Inventory (Inferred)

| Table | Eloquent Model | Purpose |
|-------|---------------|---------|
| `statistics` | `Statistics` | **Primary CDR table.** One row per call. |
| `tracings` | `Tracings` | **Append-only event log.** Multiple rows per call (one per state transition). |
| `site_ivr_numbers` | `SiteIvrNumber` | DID/IVR number registry with type and language |
| `consultant_ivr_numbers` | `ConsultantIvrNumber` | Many-to-one: DIDs → consultant |
| `consultants` | `Consultant` | Coach/consultant profiles |
| `consultant_phone_numbers` | `ConsultantPhoneNumber` | Actual PSTN numbers for outbound dial |
| `consultant_extension_details` | `ConsultantExtensionDetail` | 4-digit extension codes per consultant |
| `consultant_statistics` | `ConsultantStatistics` | Aggregate stats: total calls, total duration |
| `credits_customers` | `CreditsCustomer` | Customer credit accounts |
| `ivr_known_users` | `IvrKnownUsers` | Phone number → user mapping for direct-dial pre-auth |
| `users` | (base user model) | User accounts |
| `countries` | `Country` | Country config: VAT rate, FX, direct_number_enabled flag |
| `currency_exchange_rates` | `CurrencyExchangeRate` | FX rates as JSON blobs (latest row used) |

---

## 3. `statistics` Table — Full Schema

**Purpose:** Primary CDR record. Created at call start (`/call/validate`), updated through lifecycle events, finalized at hangup.

| Column | Inferred Type | Nullable | Source |
|--------|--------------|----------|--------|
| `id` | int PK | no | Auto |
| `unique_id` | varchar | no | FreeSWITCH UUID |
| `consultant_id` | int FK→consultants | yes | From IVR number lookup |
| `credit_customer_id` | int FK→credits_customers | yes | From customer auth |
| `user_id` | int FK→users | yes | From customer auth |
| `group_id` | int | yes | Tenant/group |
| `site_ivr_number_id` | int FK→site_ivr_numbers | no | The dialed DID |
| `provider_id` | int | no | 1=OVH/2=AWS (hardcoded 1 in Sofia) |
| `type_id` | int | no | 1=site/2=direct/3=SD/4=coach |
| `type` | varchar | no | 'call' constant |
| `src_number` | varchar | no | Caller ID (+ stripped) |
| `dst_number` | varchar | no | Coach's phone number |
| `extension` | int | no | 4-digit ext (0 if N/A) |
| `start_time` | datetime | no | Session start timestamp |
| `ringing_start_time` | datetime | yes | When outbound ring started |
| `connected_time` | datetime | yes | When coach/SD answered |
| `hangup_time` | datetime | yes | When destination leg hung up |
| `end_time` | datetime | yes | When caller leg hung up |
| `total_duration` | int | yes | Seconds: start → end |
| `conversation_duration` | int | yes | Seconds: connected → hangup |
| `credit_before` | decimal(10,5) | no | Credit at block start |
| `credit_after` | decimal(10,5) | no | Credit at block end |
| `coach_rate` | decimal(10,5) | no | Per-minute rate in coach currency |
| `vat_rate` | decimal(5,2) | no | Effective VAT % |
| `consultant_earning_for_minute` | decimal(10,5) | no | (call_rate/100)*commission_pct |
| `consultant_total_earning` | decimal(10,5) | yes | Computed at hangup |
| `credit_without_vat` | decimal(10,5) | yes | Net credit excl. VAT |
| `surcharge_amount` | decimal(10,5) | yes | Per-number surcharge |
| `allocated_vat_amount` | decimal(10,5) | yes | VAT portion of total charge |
| `customer_currency_code` | varchar | no | e.g., 'EUR' |
| `customer_currency_rate` | decimal(10,5) | no | FX rate vs coach currency |
| `coach_currency_code` | varchar | no | e.g., 'CHF' |
| `coach_currency_rate` | decimal(10,5) | no | Computed at hangup (1/customer_rate) |
| `company_and_coach_currency_rate` | decimal(10,5) | yes | Coach currency → EUR |
| `company_and_customer_currency_rate` | decimal(10,5) | yes | Customer currency → EUR |
| `status` | varchar | yes | NORMAL / SHORT CALL / NO ANSWER / etc. |
| `created_at` | timestamp | no | Laravel auto |
| `updated_at` | timestamp | no | Laravel auto |

---

## 4. `tracings` Table — Full Schema

**Purpose:** Append-only event log. Every state change during a call creates a new row. Never updated.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int PK | Auto |
| `statistics_id` | int FK→statistics | Parent CDR |
| `timestamp` | datetime | Event timestamp from Sofia |
| `status` | int | Event type code (see below) |
| `info` | text | Human-readable description |
| `credit_before` | decimal(10,5) | Credit balance before event |
| `credit_after` | decimal(10,5) | Credit balance after event |
| `created_at` | timestamp | Laravel auto |
| `updated_at` | timestamp | Laravel auto |

**Status codes in `tracings`:**
| Code | Name | When Written |
|------|------|-------------|
| 1 | start_time | Call arrives, statistics row created |
| 3 | ringing_start_time | Outbound dial begins |
| 4 | connected_time | Coach/SD answers |
| 5 | credit_block_updated | Time-block renewal (every ~300s) |
| 6 | hangup_time | DST leg hangs up |
| 7 | end_time | SRC leg hangs up |
| 8 | destination_number_invalid | DID not found / invalid |
| 9 | invalid_auth_attempt | Wrong PIN entered |
| 10 | customer_authenticated | PIN verified successfully |
| 11 | consultant_authenticated | Coach PIN verified |
| 12 | consultant_change_ivr_status | Coach toggled status |
| 13 | enable_direct_dial | Phone saved to ivr_known_users |

---

## 5. `site_ivr_numbers` Table

| Column | Inferred Type | Description |
|--------|--------------|-------------|
| `id` | int PK | |
| `number` | varchar | Full DID (no + prefix) |
| `type_id` | int | 1=site, 2=direct, 3=SD, 4=coach |
| `language_id` | int | 1=nl, 2=fr, 3=es, 4=en |
| `country_id` | int FK→countries | |
| `group_id` | int | Tenant ID |

---

## 6. `credits_customers` Table

| Column | Inferred Type | Description |
|--------|--------------|-------------|
| `id` | int PK | |
| `user_id` | int FK→users | |
| `credit_code` | varchar | 8-digit PIN (the auth factor) |
| `current_credits` | decimal(10,5) | Live balance (updated on every call) |
| `currency_code` | varchar | e.g., 'eur', 'chf', 'cad' |
| `is_blocked` | boolean | If true, reject all calls |
| `is_deleted` | boolean | Soft delete |

---

## 7. `consultants` Table

| Column | Inferred Type | Description |
|--------|--------------|-------------|
| `id` | int PK | |
| `user_id` | int FK→users | |
| `ivr_status` | int | 1=online, 2=busy, 3=offline |
| `call_rate` | decimal | Per-minute rate in coach's currency |
| `currency_code` | varchar | Coach payment currency |
| `commission_percentage` | decimal | % of call_rate coach earns |
| `is_blocked` | boolean | |
| `is_deleted` | boolean | Soft delete |
| `provider_sequence` | varchar | Pipe-separated list: `voxbone-outbound\|provider2` |

---

## 8. SQL Queries to Run Against Live DB

```sql
-- 1. All public tables
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;

-- 2. statistics schema
SELECT column_name, data_type, is_nullable, column_default, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'statistics'
ORDER BY ordinal_position;

-- 3. tracings schema
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'tracings'
ORDER BY ordinal_position;

-- 4. credits_customers schema
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'credits_customers'
ORDER BY ordinal_position;

-- 5. site_ivr_numbers schema
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'site_ivr_numbers'
ORDER BY ordinal_position;

-- 6. consultants schema
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'consultants'
ORDER BY ordinal_position;

-- 7. Row counts (estimate of data volume)
SELECT 
  relname AS table_name,
  n_live_tup AS estimated_rows
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY n_live_tup DESC;

-- 8. Foreign key relationships
SELECT
  kcu.table_name, kcu.column_name,
  ccu.table_name AS foreign_table, ccu.column_name AS foreign_column
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
ORDER BY kcu.table_name;

-- 9. All indexes (performance-critical for migration)
SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename;

-- 10. Active call count (operational check)
SELECT COUNT(*) as active_statistics_without_end_time
FROM statistics
WHERE end_time IS NULL;
```

---

## 9. Entity Relationship Diagram (Inferred)

```
users
 ├─ credits_customers (1:1 — customer account)
 │    └─ ivr_known_users (1:N — phone numbers for direct-dial)
 └─ consultants (1:1 — coach account)
      ├─ consultant_phone_numbers (1:N — outbound numbers)
      ├─ consultant_extension_details (1:N — 4-digit exts)
      ├─ consultant_ivr_numbers (1:N — DID assignments)
      │    └─ site_ivr_numbers (N:1 — the DID)
      └─ consultant_statistics (1:N — aggregate stats)

statistics (CDR)
 ├─ FK: credit_customer_id → credits_customers
 ├─ FK: consultant_id → consultants
 ├─ FK: user_id → users
 ├─ FK: site_ivr_number_id → site_ivr_numbers
 └─ tracings (1:N — event log)

countries
 └─ referenced by credits_customers (via user.country), site_ivr_numbers

currency_exchange_rates
 └─ latest row queried for all FX conversions (no FK)
```

---

## 10. Migration Notes

| Concern | Detail |
|---------|--------|
| `statistics` is updated multiple times per call | Not a simple append. The same row is updated at: start, auth, ringing, connected, each time-block, and hangup. FastAPI must handle this same lifecycle. |
| `tracings` is append-only (billing regulatory) | Must replicate as append-only in new system (R-BILL-02). |
| `current_credits` is mutated live | This is the balance field used for credit gating. Redis will shadow this in the new system (R-BILL-01). |
| `ivr_known_users` enables direct-dial pre-auth | The `src_number` → `user_id` mapping must be migrated to the new system or the direct-dial feature will require full PIN re-auth for all known users. |
| FX rates are a rolling table | `currency_exchange_rates` uses `latest()->first()` — need to understand how this table is populated (likely a scheduled job in the main Helios app, not Sentinel). |
| `provider_sequence` is pipe-delimited string | Stored as `"voxbone-outbound\|provider2"` in consultants table. Parse carefully on read. |
