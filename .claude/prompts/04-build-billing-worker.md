# Prompt: Build Billing Worker (Phase 4)

## Prerequisites
- Phase 2 complete (FastAPI with credit service)
- Phase 3 complete (Lua scripts working)
- You have read `.claude/context/telecom-rules.md` — billing rules section
- `docs/legacy-audit/sofia-analysis.md` documents current CDR format

## Your Role
You are building the Billing Worker: a long-running Python service that connects to
FreeSWITCH via ESL (Event Socket Layer) and processes call events to manage CDRs
and finalize billing. This service is the financial heart of the system.

## Task

Build the billing worker under `backend/billing_worker/`.

## Architecture

```
FreeSWITCH ESL ←────────────────────── Billing Worker
      │                                       │
      │ Events:                               ├── Redis (credit cache)
      │   CHANNEL_CREATE                      │   credit:{account_id}
      │   CHANNEL_ANSWER                      │   call:{call_uuid}
      │   CHANNEL_BRIDGE                      │
      │   CHANNEL_HANGUP_COMPLETE             └── PostgreSQL
      │   CHANNEL_DESTROY                         cdr table (append-only)
      ▼
  (ESL library)
```

## Files to Create

```
backend/billing_worker/
├── worker.py              # Main loop: connect ESL, subscribe, dispatch
├── config.py              # Settings
├── handlers/
│   ├── __init__.py
│   ├── call_create.py     # CHANNEL_CREATE event
│   ├── call_answer.py     # CHANNEL_ANSWER event
│   ├── call_bridge.py     # CHANNEL_BRIDGE event
│   ├── call_hangup.py     # CHANNEL_HANGUP_COMPLETE — most important
│   └── reconcile.py       # Startup reconciliation
├── esl/
│   ├── __init__.py
│   └── client.py          # ESL connection wrapper
├── services/
│   ├── cdr_service.py     # Write/finalize CDRs to PostgreSQL
│   └── credit_service.py  # Redis credit operations
├── models/
│   └── cdr.py             # CDR data model
└── tests/
    ├── test_hangup_handler.py
    ├── test_cdr_service.py
    └── test_reconcile.py
```

## Core: `worker.py`

```python
"""
Billing Worker — FreeSWITCH ESL Event Consumer

Connects to FreeSWITCH ESL and processes call lifecycle events to:
1. Track active calls in Redis
2. Finalize CDRs in PostgreSQL on hangup
3. Reconcile any orphaned sessions on startup

Telecom note: This worker must be single-instance to avoid double-billing.
ECS desired_count=1 with min_healthy_percent=0 for safe replacement.
"""
import asyncio
import logging
import structlog
from esl.client import ESLClient
from handlers.call_hangup import handle_hangup
from handlers.call_answer import handle_answer
from handlers.reconcile import reconcile_on_startup
from config import Settings

log = structlog.get_logger()

SUBSCRIPTIONS = [
    "CHANNEL_CREATE",
    "CHANNEL_ANSWER", 
    "CHANNEL_BRIDGE",
    "CHANNEL_HANGUP_COMPLETE",
    "CHANNEL_DESTROY",
]

async def main():
    settings = Settings()
    
    # Reconcile any sessions that were open before last restart
    await reconcile_on_startup(settings)
    
    client = ESLClient(
        host=settings.freeswitch_esl_host,
        port=settings.freeswitch_esl_port,
        password=settings.freeswitch_esl_password,
    )
    
    async for event in client.subscribe(SUBSCRIPTIONS):
        event_name = event.get("Event-Name")
        call_uuid = event.get("Unique-ID")
        
        log.info("esl_event", event_name=event_name, call_uuid=call_uuid)
        
        try:
            if event_name == "CHANNEL_ANSWER":
                await handle_answer(event, settings)
            elif event_name == "CHANNEL_HANGUP_COMPLETE":
                await handle_hangup(event, settings)
        except Exception as e:
            # Never crash the worker on a single event failure
            log.error("event_handler_error", 
                      event_name=event_name, 
                      call_uuid=call_uuid, 
                      error=str(e))

if __name__ == "__main__":
    asyncio.run(main())
```

## Critical: `handlers/call_hangup.py`

This is the most important handler. Every call ends here.

```python
"""
CHANNEL_HANGUP_COMPLETE handler

Triggered when a call ends (any reason: normal, credit exhaustion, error).
Responsibilities:
1. Calculate final call duration
2. Compute final cost (ceiling per second)
3. Atomically deduct final amount from Redis credit
4. Reconcile with any tick deductions already made
5. Write finalized CDR to PostgreSQL (append-only)
6. Decrement concurrent call counter in Redis
7. Delete call session from Redis

Telecom rule (R-BILL-02): CDRs are append-only. Never UPDATE a finalized CDR.
Telecom rule (R-BILL-06): Round up to ceiling per second.
"""
import math
from datetime import datetime, timezone
from typing import Any
import structlog
from models.cdr import CDR, CallDisposition
from services.cdr_service import CDRService
from services.credit_service import CreditService

log = structlog.get_logger()

async def handle_hangup(event: dict[str, Any], settings) -> None:
    call_uuid = event["Unique-ID"]
    account_id = event.get("variable_voip_account_id")
    
    if not account_id:
        # Not one of our managed calls (e.g., internal FS calls)
        log.debug("hangup_skip_unmanaged", call_uuid=call_uuid)
        return
    
    # Parse durations from FreeSWITCH event
    billsec = int(event.get("variable_billsec", 0))          # seconds answered
    duration = int(event.get("variable_duration", 0))        # total duration
    hangup_cause = event.get("Hangup-Cause", "UNKNOWN")
    caller_id = event.get("Caller-Caller-ID-Number", "")
    destination = event.get("Caller-Destination-Number", "")
    answer_epoch = event.get("variable_answer_epoch", "0")
    end_epoch = event.get("variable_end_epoch", "0")
    
    # Get rate from call session (set during auth)
    credit_svc = CreditService(settings)
    call_session = await credit_svc.get_call_session(call_uuid)
    
    if not call_session:
        log.warning("hangup_no_session", call_uuid=call_uuid, account_id=account_id)
        # Still write CDR with available data
    
    rate_per_minute = float(call_session.get("rate_per_minute", 0)) if call_session else 0
    already_deducted = int(call_session.get("deducted_cents", 0)) if call_session else 0
    
    # Calculate final cost (ceiling per second = R-BILL-06)
    rate_per_second = rate_per_minute / 60
    final_cost_cents = math.ceil(billsec * rate_per_second * 100)
    
    # Deduct remaining (final_cost - already_deducted via ticks)
    remaining_to_deduct = max(0, final_cost_cents - already_deducted)
    
    if remaining_to_deduct > 0 and account_id:
        await credit_svc.deduct_credit(account_id, remaining_to_deduct, call_uuid)
    
    # Determine disposition
    if billsec > 0:
        disposition = CallDisposition.ANSWERED
    elif hangup_cause in ("NO_ANSWER", "NO_USER_RESPONSE"):
        disposition = CallDisposition.NO_ANSWER
    elif hangup_cause == "USER_BUSY":
        disposition = CallDisposition.BUSY
    else:
        disposition = CallDisposition.FAILED
    
    # Write CDR — this is the FINAL record, never updated
    cdr = CDR(
        call_uuid=call_uuid,
        account_id=account_id,
        caller_id=caller_id,
        destination=destination,
        gateway=call_session.get("gateway") if call_session else None,
        answer_time=datetime.fromtimestamp(int(answer_epoch), tz=timezone.utc) if answer_epoch != "0" else None,
        end_time=datetime.fromtimestamp(int(end_epoch), tz=timezone.utc),
        duration_seconds=duration,
        billsec=billsec,
        hangup_cause=hangup_cause,
        disposition=disposition,
        rate_per_minute=rate_per_minute,
        cost_cents=final_cost_cents,
        is_final=True,
    )
    
    cdr_svc = CDRService(settings)
    await cdr_svc.write_cdr(cdr)
    
    # Cleanup Redis
    await credit_svc.cleanup_call_session(call_uuid)
    await credit_svc.decrement_concurrent(account_id)
    
    log.info("hangup_finalized", 
             call_uuid=call_uuid,
             account_id=account_id,
             billsec=billsec,
             cost_cents=final_cost_cents,
             disposition=disposition.value)
```

## Reconciliation on Startup

```python
"""
handlers/reconcile.py

On startup, find any call sessions in Redis that don't have a
corresponding active call in FreeSWITCH. These are orphaned sessions
from a billing worker crash — finalize their CDRs immediately.

Telecom rule (R-BILL-05).
"""
async def reconcile_on_startup(settings) -> None:
    # 1. Get all call:{uuid} keys from Redis
    # 2. Query FreeSWITCH ESL: "show calls" 
    # 3. Any Redis session not in FS active calls → run hangup handler
    # 4. Log reconciliation results
```

## CDR Model

```python
# models/cdr.py
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

class CallDisposition(str, Enum):
    ANSWERED = "ANSWERED"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

@dataclass
class CDR:
    call_uuid: str
    account_id: str
    caller_id: str
    destination: str
    gateway: str | None
    answer_time: datetime | None
    end_time: datetime
    duration_seconds: int
    billsec: int
    hangup_cause: str
    disposition: CallDisposition
    rate_per_minute: float
    cost_cents: int          # Always integer (cents × 100 = fractional cents)
    is_final: bool = True    # Must be True before writing to DB
```

## Testing Requirements

Write ALL of these tests:
- `test_hangup_answered_call` — billsec>0, correct cost calculation
- `test_hangup_no_answer` — billsec=0, zero cost, FAILED disposition
- `test_hangup_credit_exhausted` — cost > remaining credit, handles gracefully
- `test_hangup_no_session` — Redis session missing (crash recovery)
- `test_hangup_cost_ceiling` — verify math.ceil used, never floor
- `test_cdr_append_only` — assert no UPDATE statements in cdr_service
- `test_reconcile_orphaned` — simulate orphaned session, verify CDR written
- `test_concurrent_decrement` — assert concurrent counter goes to 0 on hangup

## Constraints
- Worker must auto-reconnect to ESL on disconnect (exponential backoff)
- Never crash the entire worker on a single event failure
- CDR must be written even if credit deduction fails (log the discrepancy)
- All financial math: use `Decimal` or integer cents — never float arithmetic
- ESL connection: bind to a specific FreeSWITCH IP (not 0.0.0.0)
