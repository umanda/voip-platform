# How to Run the Claude Code Prompts
> VoIP Platform Modernization — ifonix

This guide explains how to use every prompt in `.claude/prompts/` with Claude Code (VS Code).

---

## Setup

### Install Claude Code

```bash
# Install the CLI
npm install -g @anthropic-ai/claude-code

# Or install the VS Code extension:
# Extensions → search "Claude Code" → Install
```

### Open the project

```bash
code /path/to/voip-platform
```

Claude Code reads `CLAUDE.md` in the project root automatically every session.
You do not need to reference it manually — it is always active.

---

## How Each Prompt Works

Every prompt file in `.claude/prompts/` is a **complete task brief**.
You hand it to Claude Code and it executes the full task — reading legacy code,
writing new code, creating tests, and producing documentation.

**The pattern is always the same:**

```
Claude, execute .claude/prompts/<prompt-file>.md
```

For prompts that need context from a previous phase, you add that context inline.

---

## Prompt Execution Order

```
01-analyze-legacy.md        ← START HERE — must be done first
02-build-fastapi.md         ← requires 01 complete
03-migrate-perl-to-lua.md   ← requires 02 complete
04-build-billing-worker.md  ← requires 02 + 03 complete
05-dockerize.md             ← requires 01–04 complete
06-build-cdk.md             ← requires 05 complete
07-cicd-monitoring.md       ← requires 06 complete
08-cutover-plan.md          ← requires all above complete
09-lab-local-setup.md       ← run any time after 02 is done
```

> Never skip a phase. Each phase produces outputs that the next phase depends on.

---

## Prompt-by-Prompt Reference

---

### `01-analyze-legacy.md` — Legacy Audit (Phase 0)

**What it does:** Reads your Perl (Sofia) and PHP (Sentinel) codebases and produces
five audit documents in `docs/legacy-audit/`.

**Run this prompt:**

```
Claude, execute .claude/prompts/01-analyze-legacy.md fully.

The codebases are at:
- Sofia (Perl):   /home/umanda/workplace/ifonix/sofia
- Sentinel (PHP): /home/umanda/workplace/ifonix/galaxy.2.0/helios/platform/Sites/Sentinel
- DB:             galaxy_2 on localhost:5432 (credentials in .claude/context/legacy-system.md)

Do not modify any legacy files.
Do not run any write operations against the legacy DB.
```

**Outputs to verify before proceeding:**

```
docs/legacy-audit/
├── sofia-analysis.md          ← every Perl file, every HTTP call it makes
├── sentinel-analysis.md       ← every PHP endpoint, every DB query
├── schema-map.md              ← all galaxy_2 tables and columns
├── migration-equivalence.md   ← legacy function → modern equivalent table
└── risk-findings.md           ← security and reliability issues found
```

**Phase 0 is complete when you can answer:**
- What exact JSON does Sofia send to Sentinel?
- What does Sentinel return when credit is insufficient?
- What does Sofia do if Sentinel is unreachable?
- What fields does a CDR contain?

---

### `02-build-fastapi.md` — FastAPI Backend (Phases 1–2)

**What it does:** Builds the complete Python FastAPI application that replaces
the PHP Sentinel API.

**Run this prompt:**

```
Claude, read docs/legacy-audit/sofia-analysis.md and
docs/legacy-audit/schema-map.md first, then execute
.claude/prompts/02-build-fastapi.md fully.

Key facts from the Phase 0 audit:
- [paste the exact request format Sofia sends to Sentinel]
- [paste the exact response fields Sentinel returns]
- [paste the DB table names for accounts, credits, and DIDs from schema-map.md]

Map all SQLAlchemy models to the EXISTING galaxy_2 schema exactly —
same table names, same column names. Do not rename anything.
```

> Replace the bracketed placeholders with actual content from your audit docs.

**Outputs to verify:**

```bash
make dev                              # starts docker-compose
curl http://localhost:8000/health     # must return {"database":"ok","redis":"ok"}
make test                             # all tests must pass
```

**Check before proceeding:**
- `/health` shows both `database: ok` and `redis: ok`
- `/v1/call/authorize` returns a `gateway` field that matches a real FreeSWITCH gateway name
- Credit deduction tests pass (especially the atomic deduction test)

---

### `03-migrate-perl-to-lua.md` — Lua Dialplan Scripts (Phase 3)

**What it does:** Rewrites the Perl (Sofia) FreeSWITCH dialplan scripts in Lua,
including the auth flow, billing ticks, and hangup handler.

**Run this prompt:**

```
Claude, read docs/legacy-audit/sofia-analysis.md carefully —
every behavior it documents must be preserved exactly.

Then execute .claude/prompts/03-migrate-perl-to-lua.md fully.

FastAPI is running at http://localhost:8000.
The internal token is in .env (INTERNAL_TOKEN variable).
```

**Outputs to verify:**

```bash
make reload-lua                       # must succeed with no errors
fs_cli -x "show modules" | grep lua  # mod_lua must be loaded
```

Test manually:
1. Make a test call through FreeSWITCH
2. Check FastAPI logs — `/v1/call/authorize` must have been called
3. Check that call bridges correctly after auth success

**Critical checks:**
- Lua HTTP timeout is set to 2000ms (check `lib/http.lua`)
- Failed auth plays IVR prompt, does not drop silently
- All phone numbers normalized to E.164 before API call

---

### `04-build-billing-worker.md` — Billing Worker (Phase 4)

**What it does:** Builds the Python ESL event consumer that processes
FreeSWITCH call events, writes CDRs, and finalizes billing on hangup.

**Run this prompt:**

```
Claude, execute .claude/prompts/04-build-billing-worker.md fully.

Reference docs/legacy-audit/sofia-analysis.md for the CDR field names
used in the legacy system — the new CDR table must include the same fields.

FastAPI credit service is already built in backend/app/services/credit_service.py.
The billing worker should import and reuse it, not rewrite it.
```

**Outputs to verify:**

```bash
make logs-billing                     # worker must connect to ESL on startup
```

Make a test call and hang up. Verify:

```bash
# CDR written to DB
psql $DATABASE_URL -c "SELECT * FROM cdr ORDER BY created_at DESC LIMIT 1;"

# Redis session cleaned up
redis-cli KEYS "call:*"               # should be empty after hangup
```

**Critical checks:**
- On worker restart, reconciliation runs and closes any orphaned Redis sessions
- CDR is written even if credit deduction fails (log the discrepancy)
- Concurrent call counter decremented on every hangup

---

### `05-dockerize.md` — Containerize Everything (Phase 5)

**What it does:** Creates production-grade Dockerfiles, a local development
`docker-compose.yml`, and a `Makefile` with developer commands.

**Run this prompt:**

```
Claude, execute .claude/prompts/05-dockerize.md fully.

All components from Phases 1–4 are complete and tested locally.
Use multi-stage Dockerfiles for API and billing worker.
FreeSWITCH runs on EC2 in production — its Dockerfile is for local dev only.
```

**Outputs to verify:**

```bash
make build                            # all images must build without errors
make dev                              # full stack starts
make test                             # all tests pass inside Docker
curl http://localhost:8000/health     # API healthy inside container
```

**Check image sizes:**

```bash
docker images | grep voip            # API image should be < 200MB
```

---

### `06-build-cdk.md` — AWS Infrastructure (Phase 6)

**What it does:** Builds the complete AWS CDK Python application with five stacks:
network, secrets, FreeSWITCH EC2, ECS Fargate services, and monitoring.

**Run this prompt:**

```
Claude, read .claude/context/aws-target.md fully before writing any code.
Then execute .claude/prompts/06-build-cdk.md fully.

AWS account ID: [your account ID]
Region: ap-southeast-1
Environment prefix: voip-staging (for first deployment)

All Docker images are in ECR — create the ECR repos as part of this phase.
```

**Outputs to verify:**

```bash
cd infrastructure
pip install -r requirements.txt
cdk synth --all                       # must produce CloudFormation with zero errors
cdk diff --all                        # review before any deploy
```

**Before deploying to AWS:**
- Review all security group rules — especially SIP IP whitelist for Voxbone
- Confirm Elastic IP allocation for FreeSWITCH
- Verify Secrets Manager secret structure matches `aws-target.md`

---

### `07-cicd-monitoring.md` — CI/CD + Monitoring (Phases 7–8)

**What it does:** Creates GitHub Actions workflows for CI, staging deploy,
and production deploy (with manual approval). Also creates Grafana dashboards
and CloudWatch alarms.

**Run this prompt:**

```
Claude, execute .claude/prompts/07-cicd-monitoring.md fully.

GitHub repo: [your repo URL]
ECR registry: [your account ID].dkr.ecr.ap-southeast-1.amazonaws.com
Staging cluster: voip-staging
Production cluster: voip-prod
Alert email: [your ops email]
```

**Outputs to verify:**

```
.github/workflows/
├── ci.yml                # runs on every PR
├── deploy-staging.yml    # runs on merge to staging branch
└── deploy-production.yml # manual trigger only
```

Push a PR and verify `ci.yml` runs and passes in GitHub Actions.

---

### `08-cutover-plan.md` — Traffic Migration (Phase 9)

**What it does:** Produces migration execution documents — NOT code.
Checklists, step-by-step traffic migration plan, rollback procedure,
and post-cutover verification SQL.

**Run this prompt:**

```
Claude, execute .claude/prompts/08-cutover-plan.md fully.

Produce all documents under docs/cutover/.
Shadow mode comparison period: minimum 72 hours before any traffic migration.
Maintenance window: 03:00 UTC (09:00 Sri Lanka time — low traffic).
```

**Outputs to verify:**

```
docs/cutover/
├── pre-cutover-checklist.md
├── shadow-mode-results.md      ← template — you fill this in after shadow mode
├── migration-steps.md
├── rollback-procedure.md
└── post-cutover-verification.md
```

---

### `09-lab-local-setup.md` — Local Test Lab

**What it does:** Builds the complete local lab environment — Asterisk carrier
simulator, Docker compose for all services, SIPp test scenarios, seed data,
and Makefile commands.

**Run after Phase 2 is complete** (FastAPI must exist for the lab to be useful).

**Run this prompt:**

```
Claude, read CLAUDE.md, then read .claude/context/architecture.md,
then execute .claude/prompts/09-lab-local-setup.md fully.

Additional context:
- Dev machine: Ubuntu 22.04, 16GB RAM
- IP Phones: Fanvil (enable UDP+TCP on SIP profile, alaw first, 60s register expiry)
- Adjust seed-lab-data.sql table and column names to match
  docs/legacy-audit/schema-map.md exactly
```

**Outputs to verify:**

```bash
cd lab
bash scripts/setup-lab.sh            # first time setup
make -f Makefile.lab lab             # start lab
curl http://localhost:8000/health    # FastAPI healthy
fs_cli -x "sofia status"             # SIP profiles loaded
```

Then configure your Fanvil phones (see `docs/lab-setup-guide.md`).

---

## Tips for Working with Claude Code

### If Claude gets confused mid-task

Re-anchor it:

```
Stop. Re-read CLAUDE.md and .claude/context/telecom-rules.md.
Then continue from where you left off.
```

### If Claude starts renaming DB columns

```
Stop. The SQLAlchemy models must use the EXACT column names from
docs/legacy-audit/schema-map.md. Do not rename anything.
```

### If Claude suggests Python inside FreeSWITCH

```
Stop. FreeSWITCH dialplan must use Lua only. See CLAUDE.md — Technology choices section.
Move any Python logic to the FastAPI service instead.
```

### If a phase produces errors

```
The tests are failing with this error: [paste error]
Fix the issue without changing the API contract or DB schema.
```

### To check what phase you're in

```
What phase are we currently on? What is complete and what is next?
```

### After any significant session

```
Summarize what was completed in this session and what still needs to be done
for the current phase to be considered complete.
```

---

## Phase Completion Checklist

| Phase | Prompt | Complete When |
|---|---|---|
| 0 | `01-analyze-legacy.md` | All 5 audit docs exist and audit questions answered |
| 1–2 | `02-build-fastapi.md` | `/health` ok, `/authorize` works, all tests pass |
| 3 | `03-migrate-perl-to-lua.md` | Lua loaded, test call auth works, timeout 2000ms |
| 4 | `04-build-billing-worker.md` | CDR written on hangup, reconcile runs on restart |
| 5 | `05-dockerize.md` | All images build, full stack runs in docker-compose |
| 6 | `06-build-cdk.md` | `cdk synth` zero errors, staging deployed |
| 7–8 | `07-cicd-monitoring.md` | CI passes on PR, alerts configured |
| 9 | `08-cutover-plan.md` | All cutover docs exist, shadow mode planned |
| Lab | `09-lab-local-setup.md` | Phones register, test call completes, CDR written |
