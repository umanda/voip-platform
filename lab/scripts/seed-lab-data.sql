-- ─────────────────────────────────────────────────────────────────────────────
-- lab/scripts/seed-lab-data.sql
-- Lab seed data for local testing — galaxy_2 schema
--
-- Table and column names match the Phase 0 audit (docs/legacy-audit/schema-map.md).
-- This file is mounted at /docker-entrypoint-initdb.d/99-seed.sql in the
-- postgres container and runs AFTER Alembic migrations create the schema.
--
-- Test accounts and their 8-digit credit_code PINs:
--   ID 1  Alpha    PIN 12341001  1000.00 EUR  → call connects, long test possible
--   ID 2  Beta     PIN 12341002     1.20 EUR  → call cuts at ~2 minutes
--   ID 3  Zero     PIN 12341003     0.00 EUR  → FastAPI rejects pre-call
--   ID 4  Blocked  PIN 12341004   500.00 EUR  → FastAPI rejects (is_blocked=true)
--
-- Test DIDs (site_ivr_numbers.number stored WITHOUT '+' per model convention):
--   18001000001 → DID for Alpha account
--   18001000002 → DID for Beta  account
--   18001000003 → DID for Zero  account
--   18001000004 → DID for Blocked account
--
-- All DIDs map to consultant ID 1 (the lab "coach") via consultant_ivr_numbers.
-- Consultant phone number: 1002 (routes to Fanvil Phone 2 via Asterisk → FS internal)
--
-- Redis credit cache is seeded separately by setup-lab.sh (credit_service.py
-- CREDIT_SCALE = 100_000 → 1 EUR = 100000 integer units).
-- ─────────────────────────────────────────────────────────────────────────────

-- Idempotent: skip if data already exists (allows re-run without error)
DO $$
BEGIN

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. COUNTRIES
-- Required FK for site_ivr_numbers.country_id
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO countries (id, name, currency_code, effective_vat_rate, direct_number_enabled)
VALUES (1, 'Lab Country', 'eur', 0.00, true)
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. USERS
-- Base user records required by credits_customers.user_id FK
-- and consultants.user_id FK.
-- users.is_deleted = false for all active lab accounts.
-- ─────────────────────────────────────────────────────────────────────────────

-- Customer users (IDs 1-4)
INSERT INTO users (id, status, is_deleted, group_id)
VALUES
  (1, 1, false, 1),   -- Customer: Alpha
  (2, 1, false, 1),   -- Customer: Beta
  (3, 1, false, 1),   -- Customer: Zero
  (4, 1, false, 1),   -- Customer: Blocked
  (5, 1, false, 1)    -- Consultant user
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. CREDITS_CUSTOMERS
-- Credit accounts. credit_code is the 8-digit PIN customers enter via DTMF IVR.
-- current_credits stores the settlement balance (decimal 10,5).
-- Redis credit:{id} key (integer units = current_credits * 100000) is the
-- live balance used for atomic deduction; seeded by setup-lab.sh.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO credits_customers (id, user_id, credit_code, current_credits, currency_code, is_blocked, is_deleted)
VALUES
  -- Alpha: plenty of credit — calls will connect and run without issue
  (1, 1, '12341001', 1000.00000, 'eur', false, false),

  -- Beta: 0.05 EUR — at EUR 0.02/min base + 21% VAT (0.0242/min charged) ≈ 124 s (~2 min)
  -- Use this to test billing ticks and credit exhaustion hangup (R-FLOW-02)
  (2, 2, '12341002',    0.05000, 'eur', false, false),

  -- Zero: no credit — FastAPI must reject the call before it connects (R-BILL-03)
  (3, 3, '12341003',    0.00000, 'eur', false, false),

  -- Blocked: is_blocked=true — FastAPI raises AccountSuspendedError regardless of balance
  (4, 4, '12341004',  500.00000, 'eur', true,  false)
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. SITE_IVR_NUMBERS
-- DID registry. number stored WITHOUT '+' prefix (legacy fixCliNumbers() strips it).
-- type_id = 1 (site/credit — requires PIN auth)
-- language_id = 4 (English)
-- country_id = 1 (Lab Country)
-- group_id = 1 (lab tenant)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO site_ivr_numbers (id, number, type_id, language_id, country_id, group_id)
VALUES
  (1, '18001000001', 1, 4, 1, 1),   -- DID for Alpha account (good credit)
  (2, '18001000002', 1, 4, 1, 1),   -- DID for Beta  account (low credit)
  (3, '18001000003', 1, 4, 1, 1),   -- DID for Zero  account (zero credit)
  (4, '18001000004', 1, 4, 1, 1)    -- DID for Blocked account
ON CONFLICT (id) DO UPDATE SET
  number      = EXCLUDED.number,
  type_id     = EXCLUDED.type_id,
  language_id = EXCLUDED.language_id;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. CONSULTANTS
-- The lab "coach" that receives all test calls.
-- call_rate: EUR 0.02000 per minute (chosen so Beta ~2min = ~1.20 EUR credit)
-- provider_sequence: 'asterisk-lab' — must match gateway name in FS external.xml
-- ivr_status = 1 (online/available)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO consultants (id, user_id, ivr_status, call_rate, currency_code, commission_percentage, is_blocked, is_deleted, provider_sequence)
VALUES
  (1, 5, 1, 0.02000, 'eur', 50.00, false, false, 'asterisk-lab')
ON CONFLICT (id) DO UPDATE SET
  user_id          = EXCLUDED.user_id,
  ivr_status       = EXCLUDED.ivr_status,
  call_rate        = EXCLUDED.call_rate,
  provider_sequence = EXCLUDED.provider_sequence;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. CONSULTANT_PHONE_NUMBERS
-- The actual number FreeSWITCH dials via the asterisk-lab gateway.
-- phone_number = '1002' → Asterisk [from-freeswitch] routes to FS internal ext 1002
-- (Fanvil Phone 2 — the "consultant" phone in the lab)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO consultant_phone_numbers (id, consultant_id, phone_number, is_active, surcharge_amount)
VALUES
  (1, 1, '1002', true, 0.00000)
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. CONSULTANT_IVR_NUMBERS
-- Maps DIDs to consultant. All 4 test DIDs route to consultant ID 1 (lab coach).
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO consultant_ivr_numbers (id, consultant_id, site_ivr_number_id, group_id)
VALUES
  (1, 1, 1, 1),   -- DID 18001000001 → consultant 1 (Alpha's DID)
  (2, 1, 2, 1),   -- DID 18001000002 → consultant 1 (Beta's DID)
  (3, 1, 3, 1),   -- DID 18001000003 → consultant 1 (Zero's DID)
  (4, 1, 4, 1)    -- DID 18001000004 → consultant 1 (Blocked's DID)
ON CONFLICT (id) DO NOTHING;

END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Reset sequences after explicit ID inserts
-- ─────────────────────────────────────────────────────────────────────────────
SELECT setval('countries_id_seq',           (SELECT MAX(id) FROM countries));
SELECT setval('users_id_seq',               (SELECT MAX(id) FROM users));
SELECT setval('credits_customers_id_seq',   (SELECT MAX(id) FROM credits_customers));
SELECT setval('site_ivr_numbers_id_seq',    (SELECT MAX(id) FROM site_ivr_numbers));
SELECT setval('consultants_id_seq',         (SELECT MAX(id) FROM consultants));
SELECT setval('consultant_phone_numbers_id_seq', (SELECT MAX(id) FROM consultant_phone_numbers));
SELECT setval('consultant_ivr_numbers_id_seq',   (SELECT MAX(id) FROM consultant_ivr_numbers));

-- ─────────────────────────────────────────────────────────────────────────────
-- QUICK VERIFICATION QUERY
-- Run manually to confirm seed: SELECT id, credit_code, current_credits, is_blocked
--   FROM credits_customers ORDER BY id;
-- ─────────────────────────────────────────────────────────────────────────────
