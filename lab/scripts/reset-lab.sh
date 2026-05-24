#!/bin/bash
# lab/scripts/reset-lab.sh — Wipe all lab data and restart from scratch
# ⚠ DESTRUCTIVE — deletes all Docker volumes (PostgreSQL data, Redis data)
# Run from repo root: bash lab/scripts/reset-lab.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DC="docker compose -f ${LAB_DIR}/docker-compose.lab.yml"

cd "${LAB_DIR}"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ⚠  VoIP Lab RESET — ALL DATA WILL BE DELETED"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  This will:"
echo "    - Stop all lab containers"
echo "    - Delete PostgreSQL volume (all CDRs, statistics, tracings)"
echo "    - Delete Redis volume (all credit cache)"
echo "    - Re-run migrations and seed data"
echo ""
read -rp "  Are you sure? [y/N] " answer
if [[ "${answer,,}" != "y" ]]; then
  echo "  Cancelled."
  exit 0
fi

echo ""
echo "Stopping and removing all lab containers and volumes..."
${DC} down -v --remove-orphans
echo "  ✓ Containers and volumes removed"

echo ""
echo "Running fresh setup..."
bash "${SCRIPT_DIR}/setup-lab.sh"
