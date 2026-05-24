-- scripts/db/init.sql
-- Local development database initialization for galaxy_2
--
-- This script runs automatically when the postgres container starts fresh
-- (via docker-entrypoint-initdb.d). It creates the schema and inserts
-- seed data for local testing.
--
-- Schema is derived from the SQLAlchemy models in backend/app/models/db/.
-- Column names preserved EXACTLY to match the legacy Laravel/Eloquent schema.
--
-- Seed data includes:
--   2 test credit accounts (customers with IVR PIN)
--   2 consultants with phone numbers and extension codes
--   2 DID numbers (site IVR numbers)
--   1 country (Belgium — EUR, 21% VAT)
--   1 FX rate row (EUR baseline)
--
-- In production, schema is managed by Alembic migrations.
-- This file is LOCAL DEV ONLY — never run against production.

-- ── Schema ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS countries (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR NOT NULL,
    currency_code        VARCHAR(10) NOT NULL,
    effective_vat_rate   NUMERIC(5,2) NOT NULL DEFAULT 20,
    direct_number_enabled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS users (
    id           SERIAL PRIMARY KEY,
    status       INTEGER,
    is_deleted   BOOLEAN NOT NULL DEFAULT FALSE,
    group_id     INTEGER
);

CREATE TABLE IF NOT EXISTS credits_customers (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    credit_code      VARCHAR(8) NOT NULL UNIQUE,
    current_credits  NUMERIC(10,5) NOT NULL DEFAULT 0,
    currency_code    VARCHAR(10) NOT NULL DEFAULT 'eur',
    is_blocked       BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_credits_customers_credit_code ON credits_customers(credit_code);

CREATE TABLE IF NOT EXISTS consultants (
    id                     SERIAL PRIMARY KEY,
    user_id                INTEGER NOT NULL REFERENCES users(id),
    ivr_status             INTEGER NOT NULL DEFAULT 3,   -- 1=online, 2=busy, 3=offline
    call_rate              NUMERIC(10,5) NOT NULL,
    currency_code          VARCHAR(10) NOT NULL,
    commission_percentage  NUMERIC(5,2) NOT NULL DEFAULT 0,
    is_blocked             BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted             BOOLEAN NOT NULL DEFAULT FALSE,
    provider_sequence      VARCHAR
);

CREATE TABLE IF NOT EXISTS consultant_phone_numbers (
    id               SERIAL PRIMARY KEY,
    consultant_id    INTEGER NOT NULL REFERENCES consultants(id),
    phone_number     VARCHAR NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    surcharge_amount NUMERIC(10,5) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consultant_extension_details (
    id            SERIAL PRIMARY KEY,
    consultant_id INTEGER NOT NULL REFERENCES consultants(id),
    group_id      INTEGER,
    extension     VARCHAR(4) NOT NULL
);

CREATE TABLE IF NOT EXISTS site_ivr_numbers (
    id          SERIAL PRIMARY KEY,
    number      VARCHAR NOT NULL UNIQUE,
    type_id     INTEGER NOT NULL,    -- 1=site, 2=direct, 3=SD, 4=coach, 5=premium
    language_id INTEGER NOT NULL,    -- 1=nl, 2=fr, 3=es, 4=en
    country_id  INTEGER REFERENCES countries(id),
    group_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_site_ivr_numbers_number ON site_ivr_numbers(number);

CREATE TABLE IF NOT EXISTS statistics (
    id                           SERIAL PRIMARY KEY,
    unique_id                    VARCHAR NOT NULL,
    consultant_id                INTEGER REFERENCES consultants(id),
    credit_customer_id           INTEGER REFERENCES credits_customers(id),
    user_id                      INTEGER REFERENCES users(id),
    group_id                     INTEGER,
    site_ivr_number_id           INTEGER NOT NULL REFERENCES site_ivr_numbers(id),
    provider_id                  INTEGER NOT NULL DEFAULT 1,
    type_id                      INTEGER NOT NULL,
    type                         VARCHAR NOT NULL DEFAULT 'call',
    src_number                   VARCHAR NOT NULL,
    dst_number                   VARCHAR NOT NULL,
    extension                    INTEGER NOT NULL DEFAULT 0,
    start_time                   TIMESTAMP NOT NULL,
    ringing_start_time           TIMESTAMP,
    connected_time               TIMESTAMP,
    hangup_time                  TIMESTAMP,
    end_time                     TIMESTAMP,
    total_duration               INTEGER,
    conversation_duration        INTEGER,
    credit_before                NUMERIC(10,5) NOT NULL DEFAULT 0,
    credit_after                 NUMERIC(10,5) NOT NULL DEFAULT 0,
    coach_rate                   NUMERIC(10,5) NOT NULL DEFAULT 0,
    vat_rate                     NUMERIC(5,2) NOT NULL DEFAULT 0,
    consultant_earning_for_minute NUMERIC(10,5) NOT NULL DEFAULT 0,
    consultant_total_earning     NUMERIC(10,5),
    credit_without_vat           NUMERIC(10,5),
    hangup_cause                 VARCHAR,
    status                       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_statistics_unique_id ON statistics(unique_id);

CREATE TABLE IF NOT EXISTS tracings (
    id             SERIAL PRIMARY KEY,
    statistics_id  INTEGER NOT NULL REFERENCES statistics(id),
    timestamp      TIMESTAMP NOT NULL,
    status         INTEGER NOT NULL,   -- TracingStatus codes
    info           TEXT,
    credit_before  NUMERIC(10,5),
    credit_after   NUMERIC(10,5),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS currency_exchange_rates (
    id         SERIAL PRIMARY KEY,
    rates      JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ivr_known_users (
    id              SERIAL PRIMARY KEY,
    caller_id       VARCHAR NOT NULL,
    credit_code     VARCHAR(8) NOT NULL,
    last_used_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ivr_known_users_caller_id ON ivr_known_users(caller_id);

CREATE TABLE IF NOT EXISTS consultant_ivr_numbers (
    id                  SERIAL PRIMARY KEY,
    consultant_id       INTEGER NOT NULL REFERENCES consultants(id),
    site_ivr_number_id  INTEGER NOT NULL REFERENCES site_ivr_numbers(id),
    group_id            INTEGER
);

CREATE TABLE IF NOT EXISTS consultant_statistics (
    id            SERIAL PRIMARY KEY,
    consultant_id INTEGER NOT NULL REFERENCES consultants(id),
    statistics_id INTEGER NOT NULL REFERENCES statistics(id),
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ── Seed data ─────────────────────────────────────────────────────────────────

-- Country: Belgium (EUR, 21% VAT, direct dial enabled)
INSERT INTO countries (id, name, currency_code, effective_vat_rate, direct_number_enabled)
VALUES (1, 'Belgium', 'eur', 21.00, TRUE)
ON CONFLICT (id) DO NOTHING;

-- Users: 4 base accounts (2 customers + 2 consultants)
INSERT INTO users (id, status, is_deleted, group_id) VALUES
    (1, 1, FALSE, 1),   -- customer Alice
    (2, 1, FALSE, 1),   -- customer Bob (blocked test account)
    (3, 1, FALSE, 1),   -- consultant Anna
    (4, 1, FALSE, 1)    -- consultant Marc
ON CONFLICT (id) DO NOTHING;

-- Credit customers (PIN = 8-digit credit_code)
-- Alice: 10 EUR credit, PIN 12345678 — healthy test account
-- Bob:   5 EUR credit, PIN 99999999 — blocked test account (is_blocked=true)
INSERT INTO credits_customers
    (id, user_id, credit_code, current_credits, currency_code, is_blocked, is_deleted)
VALUES
    (101, 1, '12345678', 10.00000, 'eur', FALSE, FALSE),
    (102, 2, '99999999',  5.00000, 'eur', TRUE,  FALSE)
ON CONFLICT (id) DO NOTHING;

-- Consultants
-- Anna: 1.20 EUR/min, online (ivr_status=1), voxbone gateway
-- Marc: 0.95 EUR/min, offline (ivr_status=3)
INSERT INTO consultants
    (id, user_id, ivr_status, call_rate, currency_code, commission_percentage,
     is_blocked, is_deleted, provider_sequence)
VALUES
    (1, 3, 1, 1.20000, 'eur', 30.00, FALSE, FALSE, 'voxbone-outbound'),
    (2, 4, 3, 0.95000, 'eur', 25.00, FALSE, FALSE, 'voxbone-outbound')
ON CONFLICT (id) DO NOTHING;

-- Consultant phone numbers (PSTN numbers dialed by FreeSWITCH via Voxbone)
-- These are fictional test numbers — Voxbone will reject real routing in dev
INSERT INTO consultant_phone_numbers
    (id, consultant_id, phone_number, is_active, surcharge_amount)
VALUES
    (1, 1, '32265664982', TRUE,  0.00000),   -- Anna's number
    (2, 2, '32281234567', TRUE,  0.00000)    -- Marc's number
ON CONFLICT (id) DO NOTHING;

-- Consultant extension codes (4-digit IVR shortcodes)
INSERT INTO consultant_extension_details (id, consultant_id, group_id, extension)
VALUES
    (1, 1, 1, '1001'),   -- Anna → extension 1001
    (2, 2, 1, '1002')    -- Marc → extension 1002
ON CONFLICT (id) DO NOTHING;

-- DID / IVR numbers (Voxbone DIDs — without leading '+')
-- 442071234567 — UK DID, type 1 (site/credit), English, group 1
-- 32800123456  — BE DID, type 2 (direct dial), Dutch, group 1
INSERT INTO site_ivr_numbers (id, number, type_id, language_id, country_id, group_id)
VALUES
    (1, '442071234567', 1, 4, 1, 1),   -- UK DID (site/credit IVR)
    (2, '32800123456',  2, 1, 1, 1)    -- BE DID (direct dial)
ON CONFLICT (id) DO NOTHING;

-- FX rates — EUR baseline (rate: 1.0 for EUR → EUR)
-- JSON structure mirrors what the legacy PHP scheduler inserts.
-- Add other currencies as needed for multi-currency testing.
INSERT INTO currency_exchange_rates (rates)
VALUES ('{"eur": 1.0, "usd": 1.085, "gbp": 0.863}')
ON CONFLICT DO NOTHING;

-- ── Sequence resets (ensure auto-increment starts above seed IDs) ─────────────
SELECT setval('users_id_seq',                  10, true);
SELECT setval('credits_customers_id_seq',     200, true);
SELECT setval('consultants_id_seq',            10, true);
SELECT setval('consultant_phone_numbers_id_seq', 10, true);
SELECT setval('consultant_extension_details_id_seq', 10, true);
SELECT setval('site_ivr_numbers_id_seq',       10, true);
SELECT setval('countries_id_seq',              10, true);
