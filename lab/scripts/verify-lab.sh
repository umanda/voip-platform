#!/bin/bash
# lab/scripts/verify-lab.sh — Health check all lab components
# Run from repo root: bash lab/scripts/verify-lab.sh
# Or via Makefile: make -f lab/Makefile.lab verify

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DC="docker compose -f ${LAB_DIR}/docker-compose.lab.yml"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  ✓ $1"; ((PASS+=1)); }
fail() { echo "  ✗ $1"; ((FAIL+=1)); }
warn() { echo "  ⚠ $1"; ((WARN+=1)); }

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  VoIP Lab Verification"
echo "════════════════════════════════════════════════════════════"

# ── Docker containers ─────────────────────────────────────────────────────────
echo ""
echo "Docker containers:"

for svc in postgres redis asterisk api billing-worker; do
  state=$(${DC} ps --format "{{.State}}" "${svc}" 2>/dev/null | head -1)
  if [[ "${state}" == "running" ]]; then
    ok "${svc} is running"
  else
    fail "${svc} is NOT running (state: ${state:-unknown})"
  fi
done

# FreeSWITCH is on host network — check via fs_cli
echo ""
echo "FreeSWITCH (host network):"
if command -v fs_cli &>/dev/null; then
  fs_status=$(fs_cli -x "status" 2>/dev/null || true)
  if echo "${fs_status}" | grep -q "UP"; then
    uptime=$(echo "${fs_status}" | grep "UP" | head -1)
    ok "FreeSWITCH running — ${uptime}"
    # Check internal profile
    internal_state=$(fs_cli -x "sofia status profile internal" 2>/dev/null | grep "^State" | awk '{print $2}' || echo "unknown")
    if [[ "${internal_state}" == "RUNNING" ]]; then
      ok "Internal SIP profile RUNNING (ext 1001-1005 can register)"
    else
      fail "Internal SIP profile NOT running (state: ${internal_state})"
    fi
    # Check external profile + asterisk-lab gateway
    gw_state=$(fs_cli -x "sofia status gateway asterisk-lab" 2>/dev/null | grep "^State" | awk '{print $2}' || echo "unknown")
    if [[ "${gw_state}" == "REACHABLE" || "${gw_state}" == "UP" ]]; then
      ok "Gateway asterisk-lab REACHABLE"
    else
      warn "Gateway asterisk-lab state: ${gw_state} (Asterisk may be starting)"
    fi
  else
    fail "FreeSWITCH not responding (try: fs_cli -x 'status')"
  fi
else
  warn "fs_cli not installed — cannot verify FreeSWITCH"
fi

# ── API health ────────────────────────────────────────────────────────────────
echo ""
echo "FastAPI:"
api_resp=$(curl -sf http://localhost:8000/health 2>/dev/null || echo "FAIL")
if [[ "${api_resp}" != "FAIL" ]]; then
  ok "FastAPI healthy: ${api_resp}"
else
  fail "FastAPI not responding — check: make -f lab/Makefile.lab logs-api"
fi

# ── PostgreSQL ────────────────────────────────────────────────────────────────
echo ""
echo "PostgreSQL:"
if docker exec voip-postgres pg_isready -U dev_ifx -d galaxy_2 >/dev/null 2>&1; then
  ok "PostgreSQL accepting connections"
  # Check seed data
  row_count=$(docker exec voip-postgres psql -U dev_ifx -d galaxy_2 -tAc \
    "SELECT COUNT(*) FROM credits_customers;" 2>/dev/null || echo "0")
  if [[ "${row_count}" -ge 4 ]]; then
    ok "Seed data present (${row_count} credit_customers rows)"
  else
    fail "Seed data missing! Run: docker exec voip-postgres psql -U dev_ifx -d galaxy_2 -f /docker-entrypoint-initdb.d/99-seed.sql"
  fi
else
  fail "PostgreSQL not ready"
fi

# ── Redis ─────────────────────────────────────────────────────────────────────
echo ""
echo "Redis:"
if docker exec voip-redis redis-cli PING >/dev/null 2>&1; then
  ok "Redis responding to PING"
  # Check credit keys
  c1=$(docker exec voip-redis redis-cli GET "credit:1" 2>/dev/null || echo "")
  c2=$(docker exec voip-redis redis-cli GET "credit:2" 2>/dev/null || echo "")
  c3=$(docker exec voip-redis redis-cli GET "credit:3" 2>/dev/null || echo "")
  if [[ -n "${c1}" && -n "${c2}" && "${c3}" == "0" ]]; then
    ok "Redis credit keys seeded (credit:1=${c1}, credit:2=${c2}, credit:3=${c3})"
  else
    fail "Redis credit keys missing. Run: make -f lab/Makefile.lab redis-seed-credit"
  fi
else
  fail "Redis not responding"
fi

# ── Asterisk ──────────────────────────────────────────────────────────────────
echo ""
echo "Asterisk:"
if docker exec voip-asterisk asterisk -rx "sip show peers" >/dev/null 2>&1; then
  ok "Asterisk CLI responding"
  peer_count=$(docker exec voip-asterisk asterisk -rx "sip show peers" 2>/dev/null | grep -c "^freeswitch" || echo "0")
  ok "SIP peers configured: ${peer_count}"
else
  fail "Asterisk not responding"
fi

# ── Host firewall ─────────────────────────────────────────────────────────────
echo ""
echo "Host firewall (UFW):"
if command -v ufw &>/dev/null; then
  ufw_status=$(sudo ufw status 2>/dev/null | head -1 || echo "unknown")
  if echo "${ufw_status}" | grep -q "active"; then
    # Check SIP ports
    sip_allowed=$(sudo ufw status 2>/dev/null | grep -E "5060|5080" || echo "")
    if [[ -n "${sip_allowed}" ]]; then
      ok "UFW active — SIP ports 5060/5080 open"
    else
      warn "UFW active but SIP ports may not be open. Run:"
      warn "  sudo ufw allow 5060/udp && sudo ufw allow 5060/tcp"
      warn "  sudo ufw allow 5080/udp && sudo ufw allow 16384:32768/udp"
    fi
  else
    ok "UFW not active — no firewall restrictions"
  fi
else
  warn "ufw not installed — check iptables manually if phones can't register"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Results: ${PASS} passed  ${WARN} warnings  ${FAIL} failed"
echo "════════════════════════════════════════════════════════════"
echo ""

if [[ ${FAIL} -gt 0 ]]; then
  echo "  Some checks failed. Review output above."
  echo "  Useful commands:"
  echo "    make -f lab/Makefile.lab logs-api      # FastAPI logs"
  echo "    make -f lab/Makefile.lab logs-asterisk # Asterisk logs"
  echo "    make -f lab/Makefile.lab logs-billing  # Billing worker logs"
  echo "    make -f lab/Makefile.lab fs-cli        # FreeSWITCH console"
  exit 1
else
  echo "  Lab is healthy. Ready to test!"
  echo ""
  HOST_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}' || hostname -I | awk '{print $1}')
  echo "  Fanvil phone registration:"
  echo "    SIP server: ${HOST_IP}:5060  UDP+TCP  expiry: 60s  codec: PCMA first"
  echo "    Phone 1: ext 1001 / pass 1001"
  echo "    Phone 2: ext 1002 / pass 1002"
fi
echo ""
