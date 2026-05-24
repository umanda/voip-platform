# VoIP Platform Modernization
## ifonix — FreeSWITCH + Lua + FastAPI + AWS

### Quick Start for Claude Code

This repo uses Claude Code with a structured `.claude/` configuration.
Before doing anything, Claude reads `CLAUDE.md` in the project root.

---

## How to Use This With Claude Code

### First Time Setup
```bash
# Clone and open in VS Code
git clone <this-repo>
code voip-platform/

# Install Claude Code extension or use CLI
# claude --dangerously-skip-permissions (for full file access)
```

### How to Run Each Phase

**Phase 0 — Legacy Audit (START HERE):**
```
Claude, read .claude/prompts/01-analyze-legacy.md and execute it fully.
```

**Phase 1–2 — Build FastAPI:**
```
Claude, read .claude/prompts/02-build-fastapi.md and execute it.
The Phase 0 audit is in docs/legacy-audit/.
```

**Phase 3 — Lua Scripts:**
```
Claude, read .claude/prompts/03-migrate-perl-to-lua.md and execute it.
Reference docs/legacy-audit/sofia-analysis.md for legacy behavior.
```

**Phase 4 — Billing Worker:**
```
Claude, read .claude/prompts/04-build-billing-worker.md and execute it.
```

**Phase 5 — Dockerize:**
```
Claude, read .claude/prompts/05-dockerize.md and execute it.
```

**Phase 6 — AWS CDK:**
```
Claude, read .claude/prompts/06-build-cdk.md and execute it.
```

**Phase 7–8 — CI/CD + Monitoring:**
```
Claude, read .claude/prompts/07-cicd-monitoring.md and execute it.
```

**Phase 9 — Cutover:**
```
Claude, read .claude/prompts/08-cutover-plan.md and produce all cutover documents.
```

---

## Project Structure

```
voip-platform/
│
├── CLAUDE.md                          ← Claude Code master instruction (READ FIRST)
│
├── .claude/
│   ├── context/
│   │   ├── architecture.md            ← System design and component responsibilities
│   │   ├── legacy-system.md           ← Legacy codebase locations and known behavior
│   │   ├── telecom-rules.md           ← Non-negotiable telecom constraints
│   │   ├── coding-standards.md        ← Python, Lua, CDK standards
│   │   ├── migration-goals.md         ← What success looks like, risk register
│   │   └── aws-target.md             ← AWS services, CDK structure, cost estimates
│   │
│   ├── prompts/
│   │   ├── 01-analyze-legacy.md       ← Phase 0: Audit Sofia + Sentinel
│   │   ├── 02-build-fastapi.md        ← Phase 1–2: FastAPI backend
│   │   ├── 03-migrate-perl-to-lua.md  ← Phase 3: Lua dialplan scripts
│   │   ├── 04-build-billing-worker.md ← Phase 4: ESL billing worker
│   │   ├── 05-dockerize.md            ← Phase 5: Docker + compose
│   │   ├── 06-build-cdk.md            ← Phase 6: AWS infrastructure
│   │   ├── 07-cicd-monitoring.md      ← Phase 7–8: GitHub Actions + Grafana
│   │   └── 08-cutover-plan.md         ← Phase 9: Traffic migration
│   │
│   └── outputs/                       ← Generated docs go here
│
├── docs/
│   ├── legacy-audit/                  ← Phase 0 output
│   │   ├── sofia-analysis.md
│   │   ├── sentinel-analysis.md
│   │   ├── schema-map.md
│   │   ├── migration-equivalence.md
│   │   └── risk-findings.md
│   ├── cutover/                       ← Phase 9 output
│   │   ├── pre-cutover-checklist.md
│   │   ├── shadow-mode-results.md
│   │   ├── migration-steps.md
│   │   ├── rollback-procedure.md
│   │   └── post-cutover-verification.md
│   ├── runbooks/
│   │   ├── freeswitch-restart.md
│   │   ├── billing-worker-restart.md
│   │   ├── credit-discrepancy.md
│   │   └── voxbone-trunk-down.md
│   └── adr/                           ← Architecture Decision Records
│       ├── 001-lua-over-python-in-freeswitch.md
│       ├── 002-redis-atomic-credit.md
│       └── 003-freeswitch-on-ec2-not-fargate.md
│
├── freeswitch/
│   ├── Dockerfile
│   ├── conf/
│   │   ├── vars.xml
│   │   ├── autoload_configs/
│   │   │   └── lua.conf.xml
│   │   ├── dialplan/
│   │   │   └── default.xml
│   │   └── sip_profiles/
│   │       ├── internal.xml
│   │       └── external.xml
│   ├── lua/
│   │   ├── dialplan/
│   │   │   ├── auth.lua
│   │   │   └── route.lua
│   │   ├── billing/
│   │   │   ├── tick.lua
│   │   │   └── hangup.lua
│   │   └── lib/
│   │       ├── http.lua
│   │       ├── logger.lua
│   │       ├── config.lua
│   │       └── utils.lua
│   └── sounds/
│       └── voip/
│           ├── auth_unavailable.wav
│           ├── insufficient_credit.wav
│           ├── credit_exhausted.wav
│           └── auth_failed.wav
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── dependencies.py
│   │   ├── routers/
│   │   ├── models/
│   │   ├── services/
│   │   └── core/
│   ├── billing_worker/
│   │   ├── worker.py
│   │   ├── handlers/
│   │   ├── esl/
│   │   └── services/
│   ├── tests/
│   ├── alembic/
│   ├── Dockerfile
│   ├── Dockerfile.worker
│   ├── requirements.txt
│   └── pyproject.toml
│
├── infrastructure/
│   ├── app.py
│   ├── cdk.json
│   ├── requirements.txt
│   ├── stacks/
│   ├── constructs/
│   └── config/
│
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/
│       └── dashboards/
│           └── voip-calls.json
│
├── scripts/
│   ├── deploy.sh
│   ├── db/
│   │   └── init.sql
│   └── maintenance/
│       └── reconcile-credits.py     ← Manual reconciliation tool
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── deploy-staging.yml
│       └── deploy-production.yml
│
├── docker-compose.yml
├── .env.example
├── Makefile
└── .gitignore
```

---

## Technology Stack Summary

| Layer         | Technology              | Hosting        |
|---------------|-------------------------|----------------|
| SIP/RTP       | FreeSWITCH 1.10         | EC2 t3.xlarge  |
| Dialplan      | Lua 5.1                 | EC2 (with FS)  |
| API           | Python FastAPI          | ECS Fargate    |
| Billing       | Python async worker     | ECS Fargate    |
| Cache         | Redis 7                 | ElastiCache    |
| Database      | PostgreSQL 15           | EC2 → RDS      |
| IaC           | AWS CDK Python          | GitHub Actions |
| Monitoring    | CloudWatch + Grafana    | ECS + AWS      |
| Secrets       | AWS Secrets Manager     | AWS            |
| CI/CD         | GitHub Actions          | GitHub         |
| SIP Provider  | Voxbone                 | External       |

---

## Critical Telecom Rules (Summary)
> Full rules in `.claude/context/telecom-rules.md`

1. Lua HTTP timeout: **2000ms max** — never block SIP thread longer
2. Credit deduction: **atomic Redis Lua script** — never app-level read-modify-write
3. CDRs: **append-only** — never UPDATE a finalized record
4. FreeSWITCH: **reload, never restart** during traffic
5. RTP ports: **UDP 16384–32768 inbound AND outbound** — one-way audio if missing outbound
6. FreeSWITCH: **Elastic IP required** for Voxbone SIP trunk registration

---

## Contacts & Accounts

| Resource         | Where                          |
|------------------|-------------------------------|
| Voxbone account  | [add URL]                      |
| AWS account      | [add account ID]               |
| GitHub repo      | [add URL]                      |
| Grafana          | [add URL after deployment]     |
| Alert email      | [add ops email]                |
