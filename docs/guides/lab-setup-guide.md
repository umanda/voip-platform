# Local VoIP Test Lab — Setup Guide
> Ubuntu 22.04 · Fanvil IP Phones · Docker · Asterisk + FreeSWITCH

---

## Overview

This guide sets up a complete VoIP test environment on your local Ubuntu machine.
It simulates a real PSTN/carrier using **Asterisk as a fake Voxbone SIP trunk**,
so you can test FreeSWITCH + Lua + FastAPI end-to-end without a real telco account.

```
Fanvil Phone 1 (ext 1001)          Fanvil Phone 2 (ext 1002)
        │                                    │
        └──────────────┬─────────────────────┘
                       │  LAN (192.168.x.x)
                       │
         ┌─────────────▼──────────────────────────┐
         │            Ubuntu Dev Machine           │
         │                                        │
         │   ┌────────────────────────────────┐   │
         │   │  Docker Network 172.20.0.0/24  │   │
         │   │                                │   │
         │   │  Asterisk (fake Voxbone) .50   │   │
         │   │       │ SIP trunk              │   │
         │   │       ▼                        │   │
         │   │  FreeSWITCH (host net)         │   │
         │   │       │ HTTP (Lua → API)       │   │
         │   │       ▼                        │   │
         │   │  FastAPI              .20       │   │
         │   │  Billing Worker       .25       │   │
         │   │  PostgreSQL           .30       │   │
         │   │  Redis                .40       │   │
         │   │  Grafana              .70       │   │
         │   └────────────────────────────────┘   │
         │                                        │
         │   Softphones (Linphone / Zoiper)        │
         └────────────────────────────────────────┘
```

| Component | Role | Technology |
|---|---|---|
| FreeSWITCH | SIP engine + Lua dialplan | Host network (not Docker) |
| Asterisk | Fake carrier / Voxbone simulator | Docker |
| FastAPI | Auth + billing API | Docker |
| Billing Worker | ESL event consumer + CDR writer | Docker |
| PostgreSQL | Persistent storage | Docker |
| Redis | Credit cache | Docker |
| Fanvil Phone 1 | IP phone — ext 1001 | Hardware (LAN) |
| Fanvil Phone 2 | IP phone — ext 1002 | Hardware (LAN) |
| Linphone / Zoiper | Softphones — ext 1003–1005 | Laptop / mobile |

> **Why FreeSWITCH uses host networking:**
> FreeSWITCH must use `network_mode: host` because RTP audio requires direct UDP
> access to your LAN. Bridge networking breaks audio with hardware IP phones.
> Asterisk uses bridge networking and talks to FreeSWITCH via the host IP.

---

## 1. Prerequisites

### System Requirements

| Resource | Requirement |
|---|---|
| OS | Ubuntu 22.04 LTS |
| RAM | 16 GB (all services use ~1.2 GB total) |
| Storage | 20 GB free minimum |
| Network | Wired LAN — same switch as Fanvil phones |
| Docker | Docker Engine 24+ and docker compose v2 |

### Install Host Tools

Run these on your Ubuntu machine before anything else:

```bash
sudo apt update
sudo apt install -y sngrep tcpdump wireshark-qt net-tools curl git
sudo usermod -aG wireshark $USER
newgrp wireshark
```

> **sngrep** is a terminal SIP call tracer. It is the single most useful
> debugging tool for this lab. Install it before you do anything else.

### Find Your Machine IP

This is the IP you enter in the Fanvil phone settings. Write it down.

```bash
ip addr show | grep 'inet ' | grep -v 127.0.0.1
# Example: inet 192.168.1.100/24  ← this is your SIP server address
```

---

## 2. File Placement

The Claude Code prompt is already in the correct location:

```
voip-platform/
└── .claude/
    └── prompts/
        └── 09-lab-local-setup.md    ← already here
```

Claude Code will generate the lab files at:

```
voip-platform/
└── lab/
    ├── docker-compose.lab.yml
    ├── .env.lab
    ├── Makefile.lab
    ├── asterisk/
    │   ├── Dockerfile
    │   └── conf/
    ├── freeswitch-lab/
    │   └── conf/
    ├── sipp/
    │   └── scenarios/
    └── scripts/
        ├── setup-lab.sh
        ├── seed-lab-data.sql
        ├── reset-lab.sh
        └── verify-lab.sh
```

---

## 3. Running Claude Code to Build the Lab

Open your repo in VS Code with Claude Code and paste this prompt:

```
Read CLAUDE.md, then read .claude/context/architecture.md,
then execute .claude/prompts/09-lab-local-setup.md fully.

Additional context:
- Dev machine: Ubuntu 22.04, 16GB RAM
- IP Phones: Fanvil (enable UDP+TCP on SIP profile, alaw first, 60s register expiry)
- Adjust seed-lab-data.sql table and column names to match
  docs/legacy-audit/schema-map.md exactly
```

---

## 4. Starting the Lab

### First-Time Setup (run once)

```bash
cd voip-platform/lab
cp .env.lab.example .env.lab

# Edit .env.lab — set these three values:
#   INTERNAL_TOKEN=<any random string>
#   FS_ESL_PASSWORD=ClueCon
#   INTERNAL_JWT_SECRET=<min 32 char random string>

bash scripts/setup-lab.sh
```

The setup script:
- Starts PostgreSQL and Redis
- Runs Alembic database migrations
- Seeds test accounts, DIDs, rate cards
- Seeds Redis credit cache
- Starts all remaining services
- Prints phone registration details

### Daily Start

```bash
# Core lab only
make -f lab/Makefile.lab lab

# With monitoring (Grafana + Prometheus)
make -f lab/Makefile.lab lab-monitor
```

---

## 5. Fanvil Phone Configuration

Both phones register directly to FreeSWITCH (not through Asterisk).

### Step 1 — Find the phone's IP

- Press **Menu → Status** on the phone
- Or check your router's DHCP client list
- Open browser: `http://<phone-ip>` — login: `admin / admin`

### Step 2 — SIP Account Settings

Navigate to **Account → Account 1** and set:

| Setting | Phone 1 | Phone 2 |
|---|---|---|
| SIP Server / Proxy | `<your-ubuntu-ip>` | `<your-ubuntu-ip>` |
| SIP Port | `5060` | `5060` |
| Transport | UDP | UDP |
| Username | `1001` | `1002` |
| Password | `1001` | `1002` |
| Display Name | `Phone 1` | `Phone 2` |
| Register Expiry | `60` | `60` |
| DTMF Mode | RFC 2833 | RFC 2833 |

### Step 3 — Codec Settings

Navigate to **Settings → Audio → Codec Priority**:

1. G.711a (PCMA) — Priority 1
2. G.711u (PCMU) — Priority 2
3. Disable: G.729, iLBC, G.722

### Verify Registration

After saving, the phone shows **Registered** within 10 seconds.

If it shows **Register Failed**:
```bash
# Allow SIP through firewall
sudo ufw allow 5060/udp
sudo ufw allow 5060/tcp

# Watch the SIP exchange
sudo sngrep
```

---

## 6. Softphone Setup

| Softphone | Platform | Extension | Password |
|---|---|---|---|
| Linphone | Linux / Android / iOS | 1003 | 1003 |
| Zoiper | Windows / Android / iOS | 1004 | 1004 |
| MicroSIP | Windows | 1005 | 1005 |

### Linphone

1. Open Linphone → **Use SIP Account**
2. SIP Address: `sip:1003@<your-ubuntu-ip>`
3. Password: `1003`
4. Transport: UDP
5. Codecs: Enable PCMA and PCMU only

### Zoiper

1. Add Account → **SIP**
2. Username: `1004`
3. Password: `1004`
4. Domain: `<your-ubuntu-ip>:5060`
5. Transport: UDP

---

## 7. Test DIDs and Accounts

These fake DIDs are seeded into the database and configured in Asterisk.
Dialing them simulates a real Voxbone inbound call hitting FreeSWITCH.

| DID | Account | Credit | Expected Result |
|---|---|---|---|
| `+18001000001` | Alpha | $1000.00 | Call connects ✓ |
| `+18001000002` | Beta | $1.20 | Cuts at ~2 minutes |
| `+18001000003` | Zero | $0.00 | Rejected immediately |
| `+18001000004` | Suspended | $500 | Rejected — account suspended |

---

## 8. Test Scenarios

### Scenario 1 — Basic End-to-End Call

Tests the full flow: Asterisk → FreeSWITCH → Lua auth → FastAPI → bridge.

1. On Fanvil Phone 1, dial: `+18001000001`
2. Asterisk routes to FreeSWITCH
3. FreeSWITCH runs `auth.lua` → calls FastAPI `/v1/call/authorize`
4. FastAPI checks credit → returns gateway
5. FreeSWITCH bridges to Fanvil Phone 2 (ext 1002)
6. Answer on Phone 2 → talk 30 seconds → hang up
7. Verify CDR: `make -f lab/Makefile.lab watch-cdrs`
8. Verify credit deducted: `make -f lab/Makefile.lab watch-credit`

---

### Scenario 2 — Credit Exhaustion

Tests billing ticks, Redis deduction, and graceful hangup with IVR prompt.

1. Phone 1 dials: `+18001000002` (Beta — $1.20 credit)
2. Answer on Phone 2
3. Wait ~2 minutes — you will hear the credit exhaustion IVR prompt
4. Call disconnects automatically
5. Verify CDR: `disposition = ANSWERED`, `cost_cents ≈ 120`
6. Verify Redis: `credit:acc-test-002` should be at 0

---

### Scenario 3 — Zero Credit Rejection

Tests the pre-call credit gate — call rejected before connecting.

1. Phone 1 dials: `+18001000003` (Zero account)
2. You immediately hear the insufficient credit IVR prompt
3. Call disconnects — Phone 2 never rings
4. Verify CDR: `disposition = FAILED`, `billsec = 0`, `cost_cents = 0`

---

### Scenario 4 — SIPp Load Test (10 Concurrent Calls)

Tests race conditions, atomic credit deduction, and API latency under load.

```bash
make -f lab/Makefile.lab sipp-concurrent
```

Monitor in separate terminals while running:

```bash
make -f lab/Makefile.lab watch-credit    # terminal 1 — watch Redis credit
make -f lab/Makefile.lab calls           # terminal 2 — active FS calls
make -f lab/Makefile.lab logs-billing    # terminal 3 — billing worker
```

> **What to watch:** Credit must only reach zero, never go negative.
> If it goes negative, the atomic Redis Lua script is not working correctly.
> Check `credit_service.py`.

---

## 9. Debugging Reference

| Problem | Command |
|---|---|
| Phone won't register | `sudo sngrep` — watch for REGISTER/401 exchange |
| One-way audio | `sudo tcpdump -i any udp portrange 16384-32768` |
| Call rejected unexpectedly | `make logs-api` — check /authorize response |
| Billing tick not firing | `make logs-billing` — check ESL event consumer |
| CDR not written | `make logs-billing` — check cdr_service errors |
| Credit not deducting | `make watch-credit` — watch Redis keys live |
| FreeSWITCH errors | `make logs-fs` or `make fs-cli` → `sofia status` |
| Asterisk not routing | `docker exec voip-asterisk asterisk -rx 'sip show peers'` |
| Lua reload needed | `make reload-lua` — no FreeSWITCH restart required |

### SIP Tracing with sngrep

```bash
sudo sngrep
```

Look for:
- `REGISTER` from phones → `200 OK` (registered)
- `INVITE` from Asterisk → `200 OK` from FreeSWITCH (call answered)
- `BYE` at call end → `200 OK` (clean hangup)
- `403 Forbidden` → auth failure

### FreeSWITCH Console Commands

```bash
fs_cli -x 'status'               # Is FreeSWITCH running?
fs_cli -x 'show calls'           # List active calls
fs_cli -x 'sofia status'         # SIP profile status + registrations
fs_cli -x 'show registrations'   # All registered phones
fs_cli -x 'reload mod_lua'       # Hot-reload Lua scripts (no restart)
fs_cli -x 'reloadxml'            # Reload dialplan XML (no restart)
fs_cli                           # Open interactive console
```

---

## 10. Monitoring

```bash
make -f lab/Makefile.lab lab-monitor
```

| Interface | URL |
|---|---|
| Grafana | http://localhost:3000 (admin / admin) |
| Prometheus | http://localhost:9090 |
| FastAPI docs | http://localhost:8000/docs |
| FastAPI health | http://localhost:8000/health |

---

## 11. Quick Reference

### Make Commands

```bash
make -f lab/Makefile.lab lab              # Start core lab
make -f lab/Makefile.lab lab-monitor      # Start lab + monitoring
make -f lab/Makefile.lab reset            # Wipe data and restart fresh
make -f lab/Makefile.lab fs-cli           # Open FreeSWITCH console
make -f lab/Makefile.lab calls            # Show active calls
make -f lab/Makefile.lab reload-lua       # Hot-reload Lua scripts
make -f lab/Makefile.lab sipp-basic       # Single test call via SIPp
make -f lab/Makefile.lab sipp-concurrent  # 10 concurrent calls via SIPp
make -f lab/Makefile.lab watch-credit     # Watch Redis credit balances
make -f lab/Makefile.lab watch-cdrs       # Watch PostgreSQL CDRs
make -f lab/Makefile.lab sngrep           # Open SIP tracer
make -f lab/Makefile.lab logs-api         # Tail FastAPI logs
make -f lab/Makefile.lab logs-billing     # Tail billing worker logs
make -f lab/Makefile.lab logs-fs          # Tail FreeSWITCH logs
```

### Extension Directory

| Extension | Device | Purpose |
|---|---|---|
| 1001 | Fanvil IP Phone 1 | Primary test caller |
| 1002 | Fanvil IP Phone 2 | Call recipient |
| 1003 | Linphone (laptop/mobile) | Softphone caller |
| 1004 | Zoiper (laptop/mobile) | Softphone caller |
| 1005 | MicroSIP or spare | Load test / extra |

### Test Account Tokens

| Token | Account | Use |
|---|---|---|
| `test-token-alpha-001` | Alpha ($1000) | Normal call testing |
| `test-token-beta-002` | Beta ($1.20) | Credit exhaustion testing |
| `test-token-zero-003` | Zero ($0) | Rejection testing |
| `test-token-suspended-004` | Suspended | Suspension testing |
