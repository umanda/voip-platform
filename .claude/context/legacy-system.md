# Legacy System Context

## Overview

The current system is a live production VoIP platform operating under the ifonix brand.
It processes real PSTN calls via Voxbone SIP trunks routed through FreeSWITCH.

## Component Map

### Sofia (Perl — FreeSWITCH dialplan)
- **Location:** `/home/umanda/workplace/ifonix/sofia`
- **Role:** FreeSWITCH dialplan scripting via Perl ESL/mod_perl
- **Key behaviors:**
  - Intercepts inbound SIP calls
  - Makes HTTP calls to Sentinel (PHP API) for auth and routing
  - Reads response to set outbound gateway
  - Handles credit exhaustion (call hangup signal)
  - Processes FreeSWITCH ESL events

### Helios (Laravel — PHP application)
- **Location:** `/home/umanda/workplace/ifonix/galaxy.2.0/helios`
- **Role:** Main application server (users, accounts, admin)

### Sentinel (PHP/Laravel — internal API consumed by Sofia)
- **Location:** `/home/umanda/workplace/ifonix/galaxy.2.0/helios/platform/Sites/Sentinel`
- **Role:** Bridge between FreeSWITCH/Perl and the database
- **Key endpoints (to be reverse-engineered):**
  - Authentication of callers
  - Credit balance retrieval
  - Routing rules lookup
  - CDR submission

## Database (PostgreSQL — galaxy_2)

```
Host:     localhost (EC2)
Port:     5432
Database: galaxy_2
User:     dev_ifx
Password: [stored in AWS Secrets Manager — DO NOT hardcode]
```

### Key tables to map (investigate from Laravel migrations):
- `users` / `accounts` — account holder info
- `dids` or `numbers` — Voxbone DID assignments
- `call_logs` or `cdrs` — call detail records
- `credits` or `balances` — account credit
- `gateways` or `routes` — outbound routing rules
- `rate_cards` — per-destination rates

> **Task for Phase 0:** Run schema discovery queries and document all tables.

## Known Behavior to Preserve

1. **Inbound DID → Account mapping** — each Voxbone number maps to one account
2. **Pre-call credit check** — call must not connect if credit < minimum threshold
3. **In-call credit deduction** — deducted periodically (identify current interval)
4. **Max duration enforcement** — call cut at credit exhaustion
5. **CDR recording** — every call logged with duration, cost, routing info
6. **Outbound gateway selection** — based on destination prefix or account config

## Known Risks in Legacy Code

- Perl HTTP calls are synchronous — potential for call setup latency
- No Redis — all credit reads go directly to PostgreSQL (N+1 risk)
- Unclear rollback behavior on DB failure during billing
- Possible race condition on concurrent calls from same account
- No structured logging — tail -f era debugging
- Credentials possibly hardcoded in Perl/PHP config files

## Migration Audit Checklist

When analyzing legacy code, answer these questions:

- [ ] What HTTP endpoints does Sofia call?
- [ ] What parameters does it send?
- [ ] What does it do with each response field?
- [ ] How does it handle HTTP timeout / connection failure?
- [ ] How does it handle insufficient credit response?
- [ ] What FreeSWITCH ESL events does it subscribe to?
- [ ] What does the CDR submission look like?
- [ ] Are there any IVR / audio prompt flows?
- [ ] Are there any country/prefix-based routing rules?
- [ ] Are there concurrent call limits per account?
