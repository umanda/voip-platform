# Prompt: Analyze Legacy System (Phase 0)

## Your Role
You are auditing the legacy ifonix VoIP platform before any code is written.
Your output will be the source of truth for all subsequent migration work.
Be thorough. Be precise. Flag every assumption.

## Task

Perform a complete audit of the following legacy codebases:

1. **Sofia (FreeSWITCH/Perl):** `/home/umanda/workplace/ifonix/sofia`
2. **Sentinel (PHP/Laravel API):** `/home/umanda/workplace/ifonix/galaxy.2.0/helios/platform/Sites/Sentinel`
3. **Database schema:** Connect to `galaxy_2` on localhost:5432 (credentials in `.claude/context/legacy-system.md`)

## Read First
- `.claude/context/legacy-system.md`
- `.claude/context/telecom-rules.md`

## Required Outputs

### Output 1: `docs/legacy-audit/sofia-analysis.md`
- Every Perl file: purpose, FreeSWITCH events it handles, HTTP calls it makes
- The exact HTTP request format to Sentinel (headers, body, auth)
- The exact response fields it reads and what it does with each
- Any hardcoded values (IPs, ports, credentials, timeouts)
- FreeSWITCH ESL event subscriptions
- IVR prompts or audio files referenced
- Error handling logic (what happens on timeout, auth failure, insufficient credit)

### Output 2: `docs/legacy-audit/sentinel-analysis.md`
- Every API endpoint: method, path, request schema, response schema
- Every DB query: table, columns read/written, any raw SQL
- Auth mechanism (how does it validate Perl's requests?)
- Business rules implemented (credit calculation, routing logic)
- Any cron jobs or background tasks

### Output 3: `docs/legacy-audit/schema-map.md`
Run these queries and document results:
```sql
-- Get all tables
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' ORDER BY table_name;

-- For each relevant table, get columns
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = '<table>'
ORDER BY ordinal_position;
```
Document: table purpose, key columns, relationships, estimated row counts.

### Output 4: `docs/legacy-audit/migration-equivalence.md`
A table mapping every legacy function/endpoint to its modern equivalent:
```
| Legacy                        | Location           | Modern Equivalent              | Notes                    |
|-------------------------------|--------------------|-------------------------------|--------------------------|
| check_credit() Perl sub       | sofia/billing.pl   | POST /v1/call/authorize        | Add Redis cache          |
| insert_cdr() Perl sub         | sofia/cdr.pl       | Billing worker HANGUP handler  | Async, not sync          |
| /api/sentinel/auth (PHP)      | Sentinel/Auth.php  | POST /v1/auth/validate         | JWT instead of session   |
```

### Output 5: `docs/legacy-audit/risk-findings.md`
Any security issues, race conditions, data integrity risks, or performance bottlenecks found.
Include severity: CRITICAL / HIGH / MEDIUM / LOW.

## Constraints
- Do NOT modify any legacy files
- Do NOT run any write operations against the legacy DB
- If you cannot access a file, document it as a gap — don't assume
- Flag any encrypted or obfuscated code that needs manual review

## Completion Check
Before finishing, verify you can answer:
- [ ] What is the exact JSON/form body Sofia sends to Sentinel?
- [ ] What does Sentinel return when credit is insufficient?
- [ ] How long does Sofia wait before timing out on the API?
- [ ] What FreeSWITCH actions does Sofia take after a successful auth?
- [ ] Where are CDRs written and what fields do they contain?
- [ ] What happens if Sentinel is unreachable during a live call?
