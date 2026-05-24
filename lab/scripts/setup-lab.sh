#!/bin/bash
# lab/scripts/setup-lab.sh — First-time lab setup
# Run from the repo root: bash lab/scripts/setup-lab.sh
# Or from lab/ directory: bash scripts/setup-lab.sh
#
# What this does:
#   1. Checks prerequisites (Docker, fs_cli, sngrep)
#   2. Creates .env.lab if missing
#   3. Starts PostgreSQL and Redis
#   4. Runs Alembic database migrations
#   5. Seeds Redis credit cache (credit_service.py CREDIT_SCALE = 100_000)
#   6. Starts all remaining services
#   7. Prints registration info for IP phones

set -euo pipefail

# ── Resolve paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${LAB_DIR}/.." && pwd)"
DC="docker compose -f ${LAB_DIR}/docker-compose.lab.yml"

cd "${LAB_DIR}"

# ─────────────────────────────────────────────────────────────────────────────
print_header() {
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  $1"
  echo "════════════════════════════════════════════════════════════"
}

print_ok()   { echo "  ✓ $1"; }
print_warn() { echo "  ⚠ $1"; }
print_err()  { echo "  ✗ $1"; }

# ─────────────────────────────────────────────────────────────────────────────
print_header "VoIP Lab Setup — Ubuntu 22.04"

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
echo ""
echo "Checking prerequisites..."

if ! command -v docker &>/dev/null; then
  print_err "Docker not found. Install: https://docs.docker.com/engine/install/ubuntu/"
  exit 1
fi
print_ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

if ! docker compose version &>/dev/null; then
  print_err "docker compose v2 not found. Upgrade Docker Engine to 24+."
  exit 1
fi
print_ok "docker compose $(docker compose version --short)"

if command -v fs_cli &>/dev/null; then
  print_ok "fs_cli found (FreeSWITCH console available)"
else
  print_warn "fs_cli not found — install FreeSWITCH client on host for 'make fs-cli'"
fi

if command -v sngrep &>/dev/null; then
  print_ok "sngrep found (SIP tracer available)"
else
  print_warn "sngrep not installed. Run: sudo apt install sngrep"
fi

# ── Step 2: Environment file ──────────────────────────────────────────────────
echo ""
echo "Checking .env.lab..."

if [ ! -f .env.lab ]; then
  cp .env.lab.example .env.lab
  print_ok "Created .env.lab from example"
  print_warn "IMPORTANT: Edit .env.lab and set INTERNAL_TOKEN, FS_ESL_PASSWORD, INTERNAL_JWT_SECRET"
  echo ""
  echo "  Edit .env.lab now, then re-run this script."
  echo "  Minimum required changes:"
  echo "    INTERNAL_TOKEN=<any 16+ char random string>"
  echo "    FS_ESL_PASSWORD=ClueCon   (or change it)"
  echo "    INTERNAL_JWT_SECRET=<32+ char random string>"
  exit 0
else
  print_ok ".env.lab exists"
fi

# Validate required variables
source .env.lab
if [[ "${INTERNAL_TOKEN:-change-me-lab-token}" == "change-me-lab-token" ]]; then
  print_err "INTERNAL_TOKEN is still the default. Edit .env.lab first."
  exit 1
fi
if [[ "${INTERNAL_JWT_SECRET:-change-me-at-least-32-chars-long-please}" == "change-me-at-least-32-chars-long-please" ]]; then
  print_err "INTERNAL_JWT_SECRET is still the default. Edit .env.lab first."
  exit 1
fi
print_ok "Environment variables validated"

# ── Step 3: SignalWire token for FreeSWITCH build ─────────────────────────────
SW_TOKEN_FILE="${REPO_ROOT}/freeswitch/.signalwire_token"
if [ ! -f "${SW_TOKEN_FILE}" ]; then
  print_warn "freeswitch/.signalwire_token not found."
  print_warn "FreeSWITCH Docker image cannot be built without it."
  echo ""
  echo "  Get your token from: https://signalwire.com (free account)"
  echo "  Then: echo 'YOUR_PAT_TOKEN' > ${SW_TOKEN_FILE}"
  echo ""
  echo "  If you already have a pre-built FreeSWITCH image, you can skip this."
  read -rp "  Continue anyway? [y/N] " answer
  if [[ "${answer,,}" != "y" ]]; then
    exit 0
  fi
fi

# ── Step 4: Start core data services ──────────────────────────────────────────
print_header "Starting PostgreSQL + Redis"

${DC} up -d postgres redis
echo "⏳ Waiting for PostgreSQL to be ready..."
timeout 60 bash -c "until docker exec voip-postgres pg_isready -U dev_ifx -d galaxy_2 &>/dev/null; do sleep 1; done"
print_ok "PostgreSQL is ready"
print_ok "Redis is running"

# ── Step 5: Run Alembic migrations ────────────────────────────────────────────
print_header "Running Database Migrations"

${DC} run --rm api alembic upgrade head
print_ok "Alembic migrations applied"

# ── Step 6: Seed Redis credit cache ───────────────────────────────────────────
print_header "Seeding Redis Credit Cache"

# CREDIT_SCALE = 100_000 (from backend/app/services/credit_service.py)
# 1 EUR credit = 100_000 integer units
#
# ID 1 — Alpha: 1000.00000 EUR = 100,000,000 units
docker exec voip-redis redis-cli SET "credit:1" 100000000 >/dev/null
print_ok "credit:1  = 100,000,000 units  (1000.00000 EUR — Alpha)"

# ID 2 — Beta: 1.20000 EUR = 120,000 units
docker exec voip-redis redis-cli SET "credit:2" 120000 >/dev/null
print_ok "credit:2  =     120,000 units  (   1.20000 EUR — Beta)"

# ID 3 — Zero: 0.00000 EUR = 0 units
docker exec voip-redis redis-cli SET "credit:3" 0 >/dev/null
print_ok "credit:3  =           0 units  (   0.00000 EUR — Zero)"

# ID 4 — Blocked: don't seed (is_blocked=true; call rejected before credit check)
print_ok "credit:4  (not seeded — account is blocked)"

# ── Step 7: Start all services ────────────────────────────────────────────────
print_header "Starting All Lab Services"

${DC} up -d
echo "⏳ Waiting for services to start..."
sleep 5

# ── Step 8: Verify ────────────────────────────────────────────────────────────
print_header "Verification"

# FastAPI health
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  print_ok "FastAPI healthy (http://localhost:8000/health)"
else
  print_err "FastAPI not responding — check: make -f lab/Makefile.lab logs-api"
fi

# Asterisk
if docker exec voip-asterisk asterisk -rx "sip show peers" >/dev/null 2>&1; then
  print_ok "Asterisk running"
else
  print_err "Asterisk not responding — check: make -f lab/Makefile.lab logs-asterisk"
fi

# PostgreSQL
if docker exec voip-postgres pg_isready -U dev_ifx -d galaxy_2 >/dev/null 2>&1; then
  print_ok "PostgreSQL running"
else
  print_err "PostgreSQL not ready"
fi

# Redis
if docker exec voip-redis redis-cli PING >/dev/null 2>&1; then
  print_ok "Redis running"
else
  print_err "Redis not responding"
fi

# FreeSWITCH
if command -v fs_cli &>/dev/null && fs_cli -x "status" 2>/dev/null | grep -q "UP"; then
  print_ok "FreeSWITCH running"
else
  print_warn "FreeSWITCH — check manually: fs_cli -x 'status'"
  print_warn "If not running, start it: docker compose -f lab/docker-compose.lab.yml start freeswitch"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
HOST_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}' || hostname -I | awk '{print $1}')

print_header "Lab Ready!"

cat <<EOF

Register your Fanvil IP phones:
  SIP Server:   ${HOST_IP}
  SIP Port:     5060
  Transport:    UDP  (TCP also enabled)
  Register Exp: 60s
  Codec:        PCMA (G.711a) first, then PCMU

  Phone 1:  ext 1001  password: 1001
  Phone 2:  ext 1002  password: 1002  ← "consultant" (receives calls)

Register softphones:
  Linphone:   sip:1003@${HOST_IP}  password: 1003
  Zoiper:     1004@${HOST_IP}:5060  password: 1004
  MicroSIP:   1005@${HOST_IP}:5060  password: 1005

Test DIDs (dial from any registered phone):
  +18001000001  → Alpha  (1000 EUR) — call connects ✓
  +18001000002  → Beta   (1.20 EUR) — cuts at ~2min
  +18001000003  → Zero   (0.00 EUR) — rejected immediately
  +18001000004  → Blocked (blocked) — rejected by FastAPI

  Tip: Fanvil Phone 1 dials +18001000001 → hear IVR → enter PIN 12341001 → Phone 2 rings

Commands:
  make -f lab/Makefile.lab verify          # health check all components
  make -f lab/Makefile.lab fs-cli          # FreeSWITCH console
  make -f lab/Makefile.lab watch-credit    # live Redis credit display
  make -f lab/Makefile.lab watch-cdrs      # live CDR (statistics) table
  make -f lab/Makefile.lab sngrep          # SIP call tracer

Monitoring (optional):
  make -f lab/Makefile.lab lab-monitor
  Grafana:     http://localhost:3000  (admin/admin)
  Prometheus:  http://localhost:9090

EOF
