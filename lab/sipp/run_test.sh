#!/bin/bash
# lab/sipp/run_test.sh — SIPp test runner convenience script
# Run from the lab/ directory: bash sipp/run_test.sh <scenario>
#
# Prerequisites:
#   docker compose -f lab/docker-compose.lab.yml --profile loadtest up -d sipp
#   OR: make -f lab/Makefile.lab lab-load

set -euo pipefail

DC="docker compose -f $(dirname "$0")/../docker-compose.lab.yml --profile loadtest"
SCENARIO="${1:-basic_call}"

usage() {
  echo "Usage: $0 <scenario>"
  echo ""
  echo "Available scenarios:"
  echo "  basic_call          — Single 30s call (DID +18001000001)"
  echo "  concurrent_calls    — 10 parallel 90s calls (stress test)"
  echo "  credit_exhaustion   — Call Beta account until credit depletes"
  echo ""
  echo "Examples:"
  echo "  $0 basic_call"
  echo "  $0 concurrent_calls"
  echo "  $0 credit_exhaustion"
  exit 1
}

if [[ "${SCENARIO}" == "-h" || "${SCENARIO}" == "--help" ]]; then
  usage
fi

SCENARIO_FILE="/scenarios/${SCENARIO}.xml"

case "${SCENARIO}" in
  basic_call)
    SIPP_ARGS="-l 1 -m 1 -r 1 -trace_msg -trace_err"
    ;;
  concurrent_calls)
    SIPP_ARGS="-l 10 -m 10 -r 2 -trace_msg"
    ;;
  credit_exhaustion)
    SIPP_ARGS="-l 1 -m 1 -r 1 -trace_msg"
    ;;
  *)
    echo "Unknown scenario: ${SCENARIO}"
    usage
    ;;
esac

echo "Running SIPp scenario: ${SCENARIO}"
echo "Arguments: ${SIPP_ARGS}"
echo ""
echo "Target: Asterisk at 172.20.0.50:5060"
echo ""

${DC} run --rm sipp \
  sipp 172.20.0.50 \
  -sf "${SCENARIO_FILE}" \
  ${SIPP_ARGS} \
  -t u1 \
  -p 5062
