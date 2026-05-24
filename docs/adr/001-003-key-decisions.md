# ADR 001: Lua Over Python for FreeSWITCH Dialplan

**Date:** 2025  
**Status:** Accepted

## Context
FreeSWITCH supports multiple scripting languages: Perl, Python, JavaScript, Lua.
The legacy system uses Perl. We need to choose the replacement.

## Decision
Use **Lua** for all FreeSWITCH dialplan and billing tick scripts.
Use **Python** for everything outside FreeSWITCH (API, worker, IaC).

## Rationale

| Factor          | Lua              | Python              |
|-----------------|------------------|---------------------|
| FS native       | ✅ Built-in mod   | ⚠️ mod_python (older)|
| Process overhead | ✅ None (in-proc) | ❌ New process each  |
| Call setup latency | ✅ < 1ms       | ❌ 50–200ms overhead |
| ESL integration | ✅ Direct         | ❌ External library  |
| Hot reload      | ✅ reload mod_lua | ❌ Restart needed    |
| Community       | ✅ FS-native docs | ⚠️ Less common path  |
| Complexity      | ✅ Simple scripts | ✅ Full framework    |

## Consequences
- Lua engineers or training required
- Lua scripts are limited in scope (dialplan + billing ticks only)
- All business logic stays in Python FastAPI (Lua just calls the API)
- Testing Lua requires FreeSWITCH running (integration tests, not unit)

---

# ADR 002: Redis Atomic Credit Deduction

**Date:** 2025  
**Status:** Accepted

## Context
Credit deduction happens on every billing tick (every 60s per call).
With many concurrent calls from the same account, race conditions can occur.

## Decision
Use **Redis Lua scripts** (server-side atomic execution) for all credit deductions.

## Rejected Alternative
Application-level: GET balance → check → SET new balance.
This has a TOCTOU race condition: two billing ticks can read the same balance
and both deduct, resulting in double deduction or under-deduction.

## Implementation
```lua
-- Redis Lua script (runs atomically on Redis server)
local balance = redis.call('GET', KEYS[1])
if not balance then return -2 end  -- account not found
if tonumber(balance) < tonumber(ARGV[1]) then return -1 end  -- insufficient
return redis.call('DECRBY', KEYS[1], ARGV[1])
```

---

# ADR 003: FreeSWITCH on EC2, Not ECS Fargate

**Date:** 2025  
**Status:** Accepted

## Context
All other services run on ECS Fargate for operational simplicity.
Decision: where to run FreeSWITCH?

## Decision
FreeSWITCH runs on **EC2**, not ECS Fargate.

## Rationale
1. **RTP/UDP:** Fargate uses bridged networking; RTP requires direct UDP with low latency. NAT through Fargate networking adds jitter.
2. **Elastic IP:** Fargate tasks get dynamic IPs. Voxbone SIP trunk requires a static IP for registration. EC2 with Elastic IP solves this cleanly.
3. **Kernel tuning:** FreeSWITCH performance requires `net.core.rmem_max` and similar sysctls. Fargate containers cannot modify kernel parameters.
4. **ESL port:** ESL runs on TCP 8021. Fargate requires ALB/NLB for inbound; ESL is not HTTP-based. EC2 SG rules are simpler.
5. **SIP NAT traversal:** Requires `ext-rtp-ip` and `ext-sip-ip` set to the public IP. EC2 + EIP makes this deterministic.

## Consequences
- FreeSWITCH EC2 requires manual patching and monitoring
- No auto-scaling (vertical scaling only — upgrade instance type)
- Deployment of Lua scripts uses S3 sync + SSM, not ECS rolling deploy
- SSM Session Manager used instead of SSH for shell access
