# Prompt: Cutover Plan — Traffic Migration (Phase 9)

## Prerequisites
- All previous phases complete
- Staging environment running and passing integration tests
- Shadow mode comparison showing < 0.1% CDR discrepancy
- Runbooks reviewed by team

## This Phase Is Different

You are NOT writing code. You are creating the migration execution plan.
Produce documents, checklists, and verification SQL — not application code.

## Outputs Required

### 1. `docs/cutover/pre-cutover-checklist.md`

Verify before starting ANY traffic migration:

**Infrastructure:**
- [ ] FreeSWITCH EC2 Elastic IP registered with Voxbone
- [ ] All ECS services running and healthy (ECS console)
- [ ] Redis ElastiCache Multi-AZ confirmed active
- [ ] All CloudWatch alarms in OK state (no pre-existing alerts)
- [ ] Secrets Manager secrets verified (test retrieval from each container)
- [ ] Billing worker reconciliation ran successfully on last restart
- [ ] Voxbone SIP trunk shows REGISTERED in FreeSWITCH (`sofia status`)

**Application:**
- [ ] `GET /health` returns `{"status": "healthy"}` for all components
- [ ] Test call end-to-end through new system (call known number, verify CDR)
- [ ] Redis credit deduction verified with test account
- [ ] CDR written to PostgreSQL after test call
- [ ] Billing tick fired at correct interval during test call

**Rollback:**
- [ ] Legacy system (Perl+PHP) still running and functional
- [ ] Rollback procedure documented and tested
- [ ] Voxbone can re-route traffic to legacy FreeSWITCH in < 5 minutes
- [ ] Team on standby during cutover window
- [ ] Maintenance window agreed (low-traffic time: e.g., 3am Sri Lanka time)

### 2. `docs/cutover/shadow-mode-results.md`

Template for documenting shadow comparison results:
```
Shadow Mode Period: [dates]
Total calls compared: [N]
CDR discrepancies: [N]
Discrepancy rate: [%]
Known discrepancy causes: [list]
Max cost difference observed: [value]
Decision: PROCEED / INVESTIGATE FURTHER
```

### 3. `docs/cutover/migration-steps.md`

Step-by-step with exact commands:

```
PHASE 9A: 10% Traffic (Day 1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Time: [YYYY-MM-DD 03:00 UTC]
Duration: 2 hours minimum before proceeding

Step 1: Route 10% of Voxbone DIDs to new FreeSWITCH
  - In Voxbone portal: select 10% of DID pool
  - Point to new FreeSWITCH Elastic IP
  - Verify: new FS shows incoming calls in "show calls"

Step 2: Monitor for 30 minutes
  - CloudWatch dashboard: voip-calls
  - Check: no billing_tick_failures alarm
  - Check: CDRs writing to new PostgreSQL
  - Check: Redis credit balances decrementing

Step 3: Compare CDRs (run every 15 min)
  [SQL query comparing legacy and new CDRs for same DIDs]

Rollback trigger:
  - Any billing_tick_failure
  - CDR discrepancy rate > 1%
  - Active call count drops unexpectedly
  - Any CRITICAL CloudWatch alarm

PHASE 9B: 50% Traffic (Day 2, if 9A clean)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 9C: 100% Traffic (Day 3, if 9B clean)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 4. `docs/cutover/rollback-procedure.md`

```
ROLLBACK PROCEDURE (execute if any trigger hit)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Time to execute: < 5 minutes

Step 1: Re-route ALL Voxbone DIDs to legacy FreeSWITCH
  [Voxbone portal steps or API call]

Step 2: Verify legacy FS receiving calls
  fs_cli -x "show calls" [on legacy box]

Step 3: Reconcile any in-flight calls on new system
  # On new billing worker:
  aws ecs update-service --cluster voip-prod --service voip-billing-worker --force-new-deployment
  # This triggers reconcile_on_startup which closes any open Redis sessions

Step 4: Audit CDRs for missed calls during rollback window
  [SQL query]

Step 5: Document what happened and open incident ticket
```

### 5. `docs/cutover/post-cutover-verification.md`

SQL queries to run after 100% cutover:
```sql
-- Verify CDR count matches expectations
SELECT 
    date_trunc('hour', created_at) as hour,
    COUNT(*) as call_count,
    SUM(cost_cents) / 100.0 as total_cost,
    AVG(billsec) as avg_duration_seconds
FROM cdr
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY 1;

-- Check for any CDRs with zero cost on answered calls (billing bug indicator)
SELECT * FROM cdr
WHERE disposition = 'ANSWERED'
AND billsec > 0
AND cost_cents = 0
AND created_at > NOW() - INTERVAL '24 hours';

-- Check concurrent call limits working
SELECT account_id, MAX(concurrent_calls) as max_concurrent
FROM call_events
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY account_id
ORDER BY max_concurrent DESC;
```

### 6. `docs/cutover/legacy-decommission.md`

After 2 weeks of stable production on new system:
- [ ] Archive legacy code to `archive/` branch
- [ ] Stop legacy FreeSWITCH (keep EC2 running for 30 days)
- [ ] Stop legacy PHP/Sentinel (keep for 30 days)
- [ ] After 30 days: terminate legacy EC2 instances
- [ ] Remove legacy DB user `dev_ifx` (if separate from new system user)
- [ ] Update DNS records (if any point to legacy)
- [ ] Close legacy monitoring
- [ ] Update team runbooks

## Constraints
- NEVER decommission legacy until 2 weeks of stable production on new system
- NEVER do cutover during business hours (Sri Lanka time)
- ALWAYS have 2 engineers available during cutover steps
- ALWAYS have Voxbone support contact ready before starting
- Shadow mode must run minimum 72 hours before ANY traffic migration
