# Lab Guide — Local VoIP Test Environment
> Ubuntu 22.04 · Fanvil IP Phones · Asterisk (fake carrier) · FreeSWITCH + Lua + FastAPI

---

## Overview

This lab provides a complete end-to-end VoIP test environment without a real Voxbone/telco account.

**Asterisk acts as the fake carrier.** It simulates Voxbone DID delivery: a test DID call goes to Asterisk, which routes it to FreeSWITCH as if it were an inbound PSTN call from a real carrier.

```
IP Phone 1 (ext 1001)             IP Phone 2 (ext 1002) ← "consultant"
      │                                    │
      └──────────── LAN ───────────────────┘
                      │ (register to FreeSWITCH :5060)
                      │
        ┌─────────────▼──────────────────────────────────┐
        │            Ubuntu 22.04 Dev Machine             │
        │                                                 │
        │  FreeSWITCH (host network)                      │
        │    :5060 — internal profile (phones + ext)       │
        │    :5080 — external profile (carrier trunk)      │
        │    :8021 — ESL (billing worker)                  │
        │    Lua scripts → HTTP → FastAPI :8001 (host)   │
        │                                                 │
        │  Docker bridge 172.28.0.0/24:                   │
        │    Asterisk        172.28.0.50  ← fake carrier  │
        │    FastAPI         172.28.0.20  :8001 (host)    │
        │    Billing Worker  172.28.0.25                  │
        │    PostgreSQL      172.28.0.30  :5434 (host)    │
        │    Redis           172.28.0.40  :6380 (host)    │
        └─────────────────────────────────────────────────┘
```

---

## Quick Start (First Time)

```bash
cd voip-platform/lab

# 1. Create and edit environment file
cp .env.lab.example .env.lab
# Edit .env.lab: set INTERNAL_TOKEN, FS_ESL_PASSWORD, INTERNAL_JWT_SECRET

# 2. Add SignalWire token (for FreeSWITCH Docker build)
echo "YOUR_PAT_TOKEN" > ../freeswitch/.signalwire_token

# 3. Run setup
bash scripts/setup-lab.sh

# 4. Verify everything is running
make -f Makefile.lab verify
```

### Daily Start

```bash
# From repo root:
make -f lab/Makefile.lab lab

# From lab/ directory:
make -f Makefile.lab lab
```

---

## Fanvil Phone Configuration

Both phones register directly to FreeSWITCH (not through Asterisk).

### Step 1 — Find your machine's IP

```bash
ip addr show | grep 'inet ' | grep -v 127.0.0.1
# e.g.: inet 192.168.1.100/24  ← use this as your SIP server address
```

### Step 2 — Open the Fanvil web interface

- Press **Menu → Status** on the phone to find its IP
- Open browser: `http://<phone-ip>` — login: `admin / admin`

### Step 3 — Account settings (Account → Account 1)

| Setting | Phone 1 | Phone 2 |
|---|---|---|
| SIP Server / Proxy | `<your-ubuntu-ip>` | `<your-ubuntu-ip>` |
| SIP Port | `5060` | `5060` |
| Transport | **UDP** (TCP also enabled on server) | **UDP** |
| Username | `1001` | `1002` |
| Password | `1001` | `1002` |
| Display Name | `Phone 1` | `Phone 2` |
| **Register Expiry** | **60** | **60** |
| DTMF Mode | RFC 2833 | RFC 2833 |
| Backup SIP Server | *(leave blank)* | *(leave blank)* |

### Step 4 — Codec settings (Settings → Audio)

Set codec priority order:
1. **G.711a (PCMA)** — Priority 1 ← most important
2. **G.711u (PCMU)** — Priority 2
3. Disable: G.729, iLBC, G.722, Opus

> **Why alaw first?** G.711a is the European standard. The lab server is
> configured with PCMA first to match Fanvil's preference.

### Step 5 — Verify registration

After saving, the phone should show **Registered** within 10 seconds.

On the server:
```bash
fs_cli -x "sofia status profile internal reg"
# Should show: sip:1001@... and sip:1002@...
```

**Troubleshooting registration failures:**
```bash
# Allow SIP through firewall
sudo ufw allow 5060/udp
sudo ufw allow 5060/tcp
sudo ufw allow 16384:32768/udp    # RTP audio

# Watch the SIP exchange
sudo sngrep
```

---

## Softphone Setup

| Softphone | Platform | Extension | Password | SIP Address |
|---|---|---|---|---|
| Linphone | Linux / Android / iOS | 1003 | 1003 | `sip:1003@<ubuntu-ip>` |
| Zoiper | Windows / Android | 1004 | 1004 | `1004@<ubuntu-ip>:5060` |
| MicroSIP | Windows | 1005 | 1005 | `1005@<ubuntu-ip>:5060` |

For all softphones: **Transport = UDP**, enable PCMA + PCMU codecs only.

---

## Test Accounts

These are seeded into the PostgreSQL `credits_customers` and `site_ivr_numbers` tables.

| DID | Credit Code (PIN) | Credit | Expected Result |
|---|---|---|---|
| `+18001000001` | `12341001` | 1000.00 EUR | Call connects ✓ |
| `+18001000002` | `12341002` | 0.05 EUR | Cuts at ~2 minutes |
| `+18001000003` | `12341003` | 0.00 EUR | Rejected before ringing |
| `+18001000004` | `12341004` | 500.00 EUR (blocked) | Rejected — account blocked |

**Consultant (Phone 2 — ext 1002):**
- All test DIDs route to consultant ID 1
- Phone number: `1002` (routes to Fanvil Phone 2 via Asterisk → FS internal)
- Rate: EUR 0.02 per minute base (0.0242/min incl. 21% VAT) — Beta 0.05 EUR = ~124 s (~2 min) max call time

---

## Test Scenarios

### Scenario 1 — Basic End-to-End Call (Manual)

Tests the full call flow: phone → DID → auth → bridge → billing.

**Steps:**
1. On Phone 1, dial: `+18001000001`
2. FreeSWITCH internal dialplan transfers to default context (auth.lua)
3. You hear the IVR: *"Welcome, please enter your 8-digit PIN"*
4. Enter DTMF: `1 2 3 4 1 0 0 1 #`
5. IVR validates with FastAPI → *"Please hold while we connect you"*
6. Phone 2 (ext 1002) rings
7. Answer on Phone 2 → talk for 30 seconds
8. Hang up on either phone

**Verify:**
```bash
make -f lab/Makefile.lab watch-cdrs       # CDR row appears in statistics table
make -f lab/Makefile.lab watch-credit     # credit:1 decreased
fs_cli -x "show calls"                    # (should show 0 after hangup)
```

---

### Scenario 2 — Credit Exhaustion (Manual)

Tests billing ticks, Redis deduction, and graceful call termination (R-FLOW-02).

1. Phone 1 dials: `+18001000002` → enter PIN `12341002`
2. Phone 2 answers
3. Wait ~2 minutes
4. You will hear: *"Your credit is running low"* at ~30s remaining
5. Then: *"Call will end in 10 seconds"*
6. Call disconnects automatically (FreeSWITCH sends SIP BYE)

**Verify:**
```bash
# credit:2 should be exactly 0 (never negative — R-BILL-01)
docker exec voip-lab-redis redis-cli GET credit:2
# Should output: 0

# CDR should show disposition=ANSWERED, cost close to 0.05 EUR
make -f lab/Makefile.lab watch-cdrs
```

---

### Scenario 3 — Zero Credit Rejection (Manual)

Tests the pre-call credit gate (R-BILL-03).

1. Phone 1 dials: `+18001000003` → enter PIN `12341003`
2. FastAPI immediately returns 402 Payment Required
3. You hear: *"Insufficient credit"* IVR prompt
4. Call disconnects — Phone 2 never rings

**Verify:**
```bash
# CDR should show disposition=FAILED, billsec=0, cost=0
make -f lab/Makefile.lab watch-cdrs
```

---

### Scenario 4 — SIPp Concurrent Call Load Test

Tests atomic credit deduction under 10 simultaneous calls.

```bash
# Terminal 1 — watch credit live
make -f lab/Makefile.lab watch-credit

# Terminal 2 — watch active FS calls
make -f lab/Makefile.lab calls

# Terminal 3 — watch billing worker logs
make -f lab/Makefile.lab logs-billing

# Terminal 4 — run the test
make -f lab/Makefile.lab sipp-concurrent
```

**Pass criteria:** `credit:1` (Alpha) decreases but never goes negative.
If it goes negative: atomic Redis Lua script in `credit_service.py` has a bug.

---

### Scenario 5 — SIPp Credit Exhaustion Automated

```bash
# Reset Beta account credit first
docker exec voip-lab-redis redis-cli SET credit:2 5000  # 0.05 EUR
psql postgresql://dev_ifx:labpass@localhost:5434/galaxy_2 \
  -c "UPDATE credits_customers SET current_credits=0.05000 WHERE id=2;"

# Run test
make -f lab/Makefile.lab sipp-credit-exhaustion
# SIPp waits for FreeSWITCH to send BYE (~2 minutes)
```

---

## SIPp Testing — PIN Auto-Entry

SIPp cannot type DTMF through the IVR like a human. For automated load tests,
configure FreeSWITCH `dialplan/auth.lua` to use a lab bypass mode:

In `freeswitch/lua/lib/config.lua`, set:
```lua
-- Lab mode: auto-supply PIN from caller ID when env is 'lab'
Config.lab_autopin = true
```

When `lab_autopin=true`, auth.lua reads the credit_code from the
`X-Lab-Pin` SIP header or derives it from the test DID number pattern.

> This mode must NEVER be enabled in production or staging.
> It is a lab-only shortcut gated on `ENVIRONMENT=lab`.

---

## Debugging Reference

### Problem: Phone won't register

```bash
# Watch SIP traffic
sudo sngrep

# Check UFW firewall
sudo ufw allow 5060/udp
sudo ufw allow 5060/tcp

# Check FreeSWITCH internal profile
fs_cli -x "sofia status profile internal"
```

### Problem: One-way audio (you hear them but they can't hear you)

```bash
# Watch RTP packets
sudo tcpdump -i any udp portrange 16384-32768
# RTP should flow both directions between phone IPs and your machine IP
```

FreeSWITCH is on host network, so RTP goes directly to/from the phone. If it's
one-way, the phone's NAT traversal settings are wrong. On Fanvil: disable symmetric
RTP if enabled, enable port reuse.

### Problem: Auth fails unexpectedly

```bash
# Check FastAPI logs
make -f lab/Makefile.lab logs-api

# Trace the authorize request
curl -s http://localhost:8001/v1/call/authorize \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: <your-INTERNAL_TOKEN>" \
  -d '{"caller_id": "+15005550001", "dialed_number": "18001000001",
       "account_token": "12341001", "inbound_did": "+18001000001"}'
```

### Problem: Billing ticks not firing

```bash
make -f lab/Makefile.lab logs-billing
# Look for: "billing tick" entries
# If missing: ESL connection to FreeSWITCH may have failed
```

### Problem: CDR not written after call

```bash
make -f lab/Makefile.lab logs-billing
# Look for: cdr_service errors
# Check: statistics table manually
docker exec voip-lab-postgres psql -U dev_ifx -d galaxy_2 \
  -c "SELECT id, unique_id, status, total_duration FROM statistics ORDER BY id DESC LIMIT 5;"
```

### Problem: credit:2 went negative

This is a **critical bug** (R-BILL-01 violation). The atomic Redis Lua script
in `backend/app/services/credit_service.py` is not working correctly.

1. Check Redis version: `docker exec voip-lab-redis redis-cli INFO server | grep redis_version`
2. Check for Lua script loading errors in API logs
3. Run the atomic deduction test: `pytest tests/test_credit.py -v`

### FreeSWITCH commands

```bash
fs_cli                              # Open console
fs_cli -x "status"                  # Uptime + call stats
fs_cli -x "show calls"              # Active calls
fs_cli -x "sofia status"            # SIP profiles
fs_cli -x "sofia status profile internal reg"  # Registered phones
fs_cli -x "sofia status gateway asterisk-lab"  # Asterisk gateway status
fs_cli -x "reload mod_lua"          # Reload Lua scripts (no restart)
fs_cli -x "reloadxml"               # Reload dialplan XML
fs_cli -x "sofia profile internal restart"  # Restart internal profile
```

---

## Architecture Notes

### Why FreeSWITCH uses host networking

FreeSWITCH must be on host network because:
1. RTP audio uses dynamic UDP ports (16384-32768) that must reach LAN phones directly
2. Docker bridge network causes NAT for RTP, breaking audio with hardware phones
3. ESL port 8021 must be reachable from billing worker (in Docker bridge)
   → the billing worker uses `host.docker.internal` to reach FS

### How the fake DID call works

When Phone 1 dials `+18001000001`:
1. FreeSWITCH internal dialplan matches the DID pattern
2. `transfer +18001000001 XML default` — re-enters the call in the default context
3. Default context runs `auth.lua` (same as a real Voxbone inbound call)
4. After auth, FS bridges to `sofia/gateway/asterisk-lab/1002`
5. Asterisk `[from-freeswitch]` context receives the call
6. Asterisk dials `SIP/1002@host.docker.internal:5060` → FS internal → Phone 2 rings

This means the call path includes Asterisk for the outbound leg only. The
inbound "carrier simulation" is done entirely within FreeSWITCH via `transfer`.
This is simpler and equally effective for testing auth + billing.

For full end-to-end carrier simulation (SIPp tests), SIPp sends INVITE to
Asterisk at port 5060, and Asterisk routes to FreeSWITCH external port 5080.

---

## File Structure

```
lab/
├── docker-compose.lab.yml      # Full lab stack
├── .env.lab.example            # Environment template
├── .env.lab                    # Your local config (gitignored)
├── Makefile.lab                # All lab commands
│
├── asterisk/
│   ├── Dockerfile              # Asterisk 20 from Debian bookworm
│   └── conf/
│       ├── asterisk.conf       # Master config (stdout logging)
│       ├── sip.conf            # FreeSWITCH as SIP peer
│       ├── extensions.conf     # DID routing + consultant routing
│       ├── rtp.conf            # RTP port range 20000-20200
│       ├── modules.conf        # Minimal module load
│       └── logger.conf         # stdout logging for Docker
│
├── freeswitch-lab/conf/
│   ├── vars.xml                # Lab global vars (asterisk-lab gateway, alaw first)
│   ├── sip_profiles/
│   │   ├── internal.xml        # Fanvil phones: UDP+TCP, PCMA first, 60s expiry
│   │   └── external.xml        # asterisk-lab gateway → Asterisk :5060
│   ├── dialplan/
│   │   ├── default.xml         # Inbound DID: auth.lua + bridge
│   │   └── internal.xml        # Extensions + DID test via transfer
│   └── directory/default/
│       ├── 1001.xml  1002.xml  # Fanvil phones
│       └── 1003.xml  1004.xml  1005.xml  # Softphones
│
├── sipp/scenarios/
│   ├── basic_call.xml          # Single 30s call
│   ├── concurrent_calls.xml    # 10 concurrent 90s calls
│   └── credit_exhaustion.xml   # Call until Beta credit depletes
│
└── scripts/
    ├── setup-lab.sh            # First-time setup
    ├── seed-lab-data.sql       # galaxy_2 schema: credits_customers, site_ivr_numbers, etc.
    ├── reset-lab.sh            # Wipe and restart
    └── verify-lab.sh           # Health check all components
```
