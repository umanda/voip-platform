# Migration Goals

## Primary Objectives

1. **Zero call flow regression** — every call behavior in the new system must match the legacy system exactly
2. **Incremental replacement** — replace one component at a time; never big-bang
3. **FreeSWITCH stays stable** — it is the one component that cannot go down
4. **Production-grade from day one** — no "we'll add monitoring later"
5. **Auditability** — every financial transaction must be traceable

## Success Criteria

| Criteria                          | Measurement                          |
|-----------------------------------|--------------------------------------|
| Call setup latency                | ≤ legacy baseline (measure first)    |
| CDR accuracy                      | 100% — zero lost CDRs               |
| Credit deduction accuracy         | 100% — no over/under billing        |
| API response time (p99)           | < 500ms for /authorize              |
| System uptime                     | 99.9% (8.7h downtime/year max)      |
| Zero credential leaks             | All secrets via Secrets Manager      |
| Observability                     | Every call traceable end-to-end      |

## What Must NOT Change (Ever)

- The E.164 number format handling
- CDR field names (external systems may read these)
- The Voxbone DID → account mapping logic
- The outbound gateway selection behavior
- The billing rounding rules (ceiling per second)

## What Can Change (With Testing)

- Internal API contracts (Perl→Lua→FastAPI)
- Database query patterns (as long as results are identical)
- Credit storage mechanism (PostgreSQL→Redis+PostgreSQL)
- Logging format (improve, never remove fields)
- Deployment mechanism (bare metal → Docker → ECS)

## Migration Sequence Logic

```
Phase 0: UNDERSTAND (read legacy code, document behavior)
    ↓
Phase 1: BUILD FOUNDATION (FastAPI scaffold, DB models, no live traffic)
    ↓
Phase 2: BUILD CORE APIs (auth + credit — test against legacy DB)
    ↓
Phase 3: BUILD LUA SCRIPTS (test in shadow mode alongside Perl)
    ↓
Phase 4: BUILD BILLING WORKER (ESL consumer, test with recorded events)
    ↓
Phase 5: CONTAINERIZE (Docker, local docker-compose, functional tests)
    ↓
Phase 6: BUILD AWS INFRA (CDK, deploy to staging)
    ↓
Phase 7: SHADOW MODE (run new system in parallel, compare CDRs)
    ↓
Phase 8: TRAFFIC MIGRATION (10% → 50% → 100%)
    ↓
Phase 9: LEGACY DECOMMISSION
```

## Risk Register

| Risk                              | Likelihood | Impact | Mitigation                              |
|-----------------------------------|------------|--------|------------------------------------------|
| CDR loss during billing worker crash | Medium  | High   | Redis persistence + reconcile on startup |
| Credit race condition              | Medium     | High   | Atomic Redis Lua scripts                 |
| FreeSWITCH restart during migration | Low      | High   | Reload commands only, pre-maintenance window |
| Voxbone trunk down during deploy   | Low        | High   | Keep legacy running until full cutover   |
| DB schema mismatch                 | Medium     | Medium | Map schema in Phase 0 before any coding  |
| Lua HTTP timeout blocking calls    | Medium     | High   | 2s hard timeout + async fallback IVR     |
| Redis data loss                    | Low        | High   | ElastiCache Multi-AZ + AOF persistence   |

## Parallel Running Strategy (Phase 7)

Run both systems simultaneously:
- Legacy (Perl+PHP): handles 100% of traffic
- New (Lua+FastAPI): shadow mode — receives copies of events, writes to shadow CDR table

Compare every hour:
```sql
SELECT 
    legacy_cdrs.call_uuid,
    legacy_cdrs.duration,
    shadow_cdrs.duration,
    legacy_cdrs.cost,
    shadow_cdrs.cost,
    ABS(legacy_cdrs.cost - shadow_cdrs.cost) as discrepancy
FROM legacy_cdrs
JOIN shadow_cdrs USING (call_uuid)
WHERE ABS(legacy_cdrs.cost - shadow_cdrs.cost) > 0.001;
```

Only proceed to traffic migration when discrepancy rate < 0.1%.
