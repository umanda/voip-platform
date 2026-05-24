# VoIP Platform and Test Lab Project
## FreeSWITCH + Lua + FastAPI + AWS

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
---

## Project Structure

```
voip-platform/
│
├── CLAUDE.md                          ← Claude Code master instruction (READ FIRST)
│
├── .claude/
│   ├── context/
│   │
│   ├── prompts/
│   │
│   └── outputs/                       ← Generated docs go here
│
├── docs/
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
