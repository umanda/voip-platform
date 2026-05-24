# Prompt: Build Local VoIP Test Lab (Lab Phase)

## Context
This is a parallel workstream to the main migration.
The lab lets you test FreeSWITCH + Lua + FastAPI + billing end-to-end
WITHOUT a real telco provider (Voxbone).

Asterisk acts as the fake carrier/SIP provider.
Everything runs in Docker except FreeSWITCH (host network for RTP).

## Read First
- `.claude/context/architecture.md`
- `.claude/context/telecom-rules.md`

---

## Target Lab Architecture

```
Physical Network (your LAN)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    
  IP Phone 1          IP Phone 2
  (ext 1001)          (ext 1002)
      │                   │
      └─────────┬─────────┘
                │ 192.168.x.x (LAN)
                │
  ┌─────────────▼─────────────────────────────┐
  │         Dev Machine                        │
  │                                            │
  │  ┌─────────────────────────────────────┐  │
  │  │   Docker Network: 172.20.0.0/24     │  │
  │  │                                     │  │
  │  │  Asterisk        172.20.0.50        │  │
  │  │  (fake carrier)                     │  │
  │  │       │ SIP trunk                   │  │
  │  │       ▼                             │  │
  │  │  FreeSWITCH      172.20.0.10        │  │
  │  │  (host network)                     │  │
  │  │       │ HTTP (Lua→API)              │  │
  │  │       ▼                             │  │
  │  │  FastAPI          172.20.0.20       │  │
  │  │       │                             │  │
  │  │       ├──► PostgreSQL  172.20.0.30  │  │
  │  │       └──► Redis       172.20.0.40  │  │
  │  │                                     │  │
  │  │  Billing Worker   172.20.0.25       │  │
  │  │  (ESL → FS)                         │  │
  │  │                                     │  │
  │  │  Grafana          172.20.0.70       │  │
  │  │  Prometheus       172.20.0.71       │  │
  │  │                                     │  │
  │  │  SIPp             172.20.0.60       │  │
  │  │  (load testing)                     │  │
  │  └─────────────────────────────────────┘  │
  │                                            │
  │  Softphones (Linphone/Zoiper)              │
  │  running on same machine or LAN            │
  └────────────────────────────────────────────┘
```

---

## Files to Create

```
lab/
├── docker-compose.lab.yml          # Full lab stack
├── .env.lab                        # Lab environment variables
├── Makefile.lab                    # Lab-specific commands
│
├── asterisk/                       # Fake carrier
│   ├── Dockerfile
│   └── conf/
│       ├── sip.conf                # Trunk to FreeSWITCH
│       ├── extensions.conf         # DID routing rules
│       ├── rtp.conf
│       └── logger.conf
│
├── freeswitch-lab/                 # Lab-specific FS config
│   └── conf/
│       ├── vars.xml
│       ├── sip_profiles/
│       │   ├── internal.xml        # IP phones + softphones register here
│       │   └── external.xml        # Trunk to Asterisk carrier
│       ├── dialplan/
│       │   ├── default.xml         # Inbound from Asterisk DIDs
│       │   └── internal.xml        # Internal extensions
│       └── directory/
│           └── default/
│               ├── 1001.xml        # IP Phone 1
│               ├── 1002.xml        # IP Phone 2
│               ├── 1003.xml        # Softphone 1 (Linphone laptop)
│               ├── 1004.xml        # Softphone 2 (Linphone mobile)
│               └── 1005.xml        # Softphone 3 (Zoiper)
│
├── sipp/                           # Load test scenarios
│   ├── scenarios/
│   │   ├── basic_call.xml          # Single call test
│   │   ├── concurrent_calls.xml    # 10 concurrent calls
│   │   └── credit_exhaustion.xml   # Call until credit runs out
│   └── run_test.sh
│
├── scripts/
│   ├── setup-lab.sh                # First-time setup
│   ├── seed-lab-data.sql           # Test accounts, DIDs, credits
│   ├── reset-lab.sh                # Wipe and restart clean
│   └── verify-lab.sh               # Health check all components
│
└── docs/
    └── lab-guide.md                # Step-by-step usage guide
```

---

## File 1: `lab/docker-compose.lab.yml`

```yaml
version: "3.9"

networks:
  voip-lab:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/24

services:

  # ─────────────────────────────────────────
  # FAKE CARRIER (simulates Voxbone)
  # ─────────────────────────────────────────
  asterisk:
    build: ./asterisk
    container_name: voip-asterisk
    networks:
      voip-lab:
        ipv4_address: 172.20.0.50
    ports:
      - "5080:5060/udp"     # SIP (different port to avoid conflict with FS)
      - "5080:5060/tcp"
    environment:
      - FREESWITCH_IP=172.20.0.10
    volumes:
      - ./asterisk/conf:/etc/asterisk
    restart: unless-stopped

  # ─────────────────────────────────────────
  # FREESWITCH
  # host network = required for RTP + IP phones on LAN
  # ─────────────────────────────────────────
  freeswitch:
    build:
      context: ../freeswitch
      dockerfile: Dockerfile
    container_name: voip-freeswitch
    network_mode: host        # REQUIRED for RTP and LAN phones
    environment:
      - API_BASE_URL=http://127.0.0.1:8000
      - INTERNAL_TOKEN=${INTERNAL_TOKEN}
      - ASTERISK_IP=127.0.0.1
      - ASTERISK_PORT=5080
    volumes:
      - ../freeswitch/lua:/usr/share/freeswitch/scripts
      - ./freeswitch-lab/conf:/etc/freeswitch
      - freeswitch-logs:/var/log/freeswitch
    restart: unless-stopped

  # ─────────────────────────────────────────
  # FASTAPI
  # ─────────────────────────────────────────
  api:
    build:
      context: ../backend
      dockerfile: Dockerfile
    container_name: voip-api
    networks:
      voip-lab:
        ipv4_address: 172.20.0.20
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://dev_ifx:labpass@172.20.0.30:5432/galaxy_2
      - REDIS_URL=redis://172.20.0.40:6379
      - INTERNAL_JWT_SECRET=${INTERNAL_JWT_SECRET}
      - ENVIRONMENT=lab
    depends_on:
      - postgres
      - redis
    volumes:
      - ../backend/app:/app/app   # hot reload
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    restart: unless-stopped

  # ─────────────────────────────────────────
  # BILLING WORKER
  # ─────────────────────────────────────────
  billing-worker:
    build:
      context: ../backend
      dockerfile: Dockerfile.worker
    container_name: voip-billing-worker
    networks:
      voip-lab:
        ipv4_address: 172.20.0.25
    environment:
      - DATABASE_URL=postgresql+asyncpg://dev_ifx:labpass@172.20.0.30:5432/galaxy_2
      - REDIS_URL=redis://172.20.0.40:6379
      - FREESWITCH_ESL_HOST=127.0.0.1   # host network FS
      - FREESWITCH_ESL_PORT=8021
      - FREESWITCH_ESL_PASSWORD=${FS_ESL_PASSWORD}
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  # ─────────────────────────────────────────
  # POSTGRESQL
  # ─────────────────────────────────────────
  postgres:
    image: postgres:15
    container_name: voip-postgres
    networks:
      voip-lab:
        ipv4_address: 172.20.0.30
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_DB=galaxy_2
      - POSTGRES_USER=dev_ifx
      - POSTGRES_PASSWORD=labpass
    volumes:
      - postgres-lab-data:/var/lib/postgresql/data
      - ./scripts/seed-lab-data.sql:/docker-entrypoint-initdb.d/seed.sql
    restart: unless-stopped

  # ─────────────────────────────────────────
  # REDIS
  # ─────────────────────────────────────────
  redis:
    image: redis:7-alpine
    container_name: voip-redis
    networks:
      voip-lab:
        ipv4_address: 172.20.0.40
    ports:
      - "6379:6379"
    command: redis-server --appendonly yes --loglevel verbose
    volumes:
      - redis-lab-data:/data
    restart: unless-stopped

  # ─────────────────────────────────────────
  # SIPP — Load testing
  # ─────────────────────────────────────────
  sipp:
    image: ctaloi/sipp:latest
    container_name: voip-sipp
    networks:
      voip-lab:
        ipv4_address: 172.20.0.60
    volumes:
      - ./sipp/scenarios:/scenarios
    profiles: ["loadtest"]    # only starts with: docker compose --profile loadtest up
    stdin_open: true
    tty: true

  # ─────────────────────────────────────────
  # PROMETHEUS
  # ─────────────────────────────────────────
  prometheus:
    image: prom/prometheus:latest
    container_name: voip-prometheus
    networks:
      voip-lab:
        ipv4_address: 172.20.0.71
    ports:
      - "9090:9090"
    volumes:
      - ../monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    profiles: ["monitoring"]

  # ─────────────────────────────────────────
  # GRAFANA
  # ─────────────────────────────────────────
  grafana:
    image: grafana/grafana:latest
    container_name: voip-grafana
    networks:
      voip-lab:
        ipv4_address: 172.20.0.70
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_AUTH_ANONYMOUS_ENABLED=true
    volumes:
      - grafana-lab-data:/var/lib/grafana
      - ../monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
    profiles: ["monitoring"]

volumes:
  postgres-lab-data:
  redis-lab-data:
  freeswitch-logs:
  grafana-lab-data:
```

---

## File 2: `lab/asterisk/conf/sip.conf`

```ini
[general]
context=from-carrier
bindport=5060
bindaddr=0.0.0.0
srvlookup=no
disallow=all
allow=ulaw
allow=alaw
nat=force_rport,comedia
qualify=yes
qualifyfreq=30

; ─────────────────────────────────────────
; FreeSWITCH as a SIP trunk peer
; Asterisk → FreeSWITCH (simulating Voxbone → FS)
; ─────────────────────────────────────────
[freeswitch-trunk]
type=peer
host=${FREESWITCH_IP}
port=5060
insecure=port,invite
context=from-carrier
disallow=all
allow=ulaw
allow=alaw
dtmfmode=rfc2833
```

---

## File 3: `lab/asterisk/conf/extensions.conf`

```ini
; ─────────────────────────────────────────
; Asterisk dialplan — simulates Voxbone DID routing
; Fake DIDs → FreeSWITCH
; ─────────────────────────────────────────

[globals]
FREESWITCH_IP=172.20.0.10

; Inbound from softphones/SIPp to Asterisk
[from-internal]
; Softphone calls a DID → Asterisk routes to FreeSWITCH
exten => _+1XXXXXXXXXX,1,NoOp(Inbound DID: ${EXTEN})
 same => n,Set(CALLERID(num)=${CALLERID(num)})
 same => n,Dial(SIP/freeswitch-trunk/${EXTEN})
 same => n,Hangup()

; Local extension shortcuts (dial 9+DID from softphone)
exten => _9XXXXXXXXXX,1,Dial(SIP/freeswitch-trunk/+1${EXTEN:1})
 same => n,Hangup()

; ─────────────────────────────────────────
; Test DIDs (fake Voxbone DIDs)
; ─────────────────────────────────────────
[from-carrier]
; DID: +18001000001 → FreeSWITCH (test account 1)
exten => +18001000001,1,NoOp(Test DID 1 - Account 1)
 same => n,Dial(SIP/freeswitch-trunk/+18001000001)
 same => n,Hangup()

; DID: +18001000002 → FreeSWITCH (test account 2)
exten => +18001000002,1,NoOp(Test DID 2 - Account 2)
 same => n,Dial(SIP/freeswitch-trunk/+18001000002)
 same => n,Hangup()

; DID: +18001000003 → FreeSWITCH (zero credit account)
exten => +18001000003,1,NoOp(Test DID 3 - Zero Credit)
 same => n,Dial(SIP/freeswitch-trunk/+18001000003)
 same => n,Hangup()

; DID: +18001000004 → FreeSWITCH (billing tick test)
exten => +18001000004,1,NoOp(Test DID 4 - Billing Test)
 same => n,Dial(SIP/freeswitch-trunk/+18001000004)
 same => n,Hangup()

; Outbound from FreeSWITCH → Asterisk → back to extensions
; Simulates outbound PSTN routing
[from-freeswitch]
exten => _+.,1,NoOp(Outbound from FS: ${EXTEN})
 same => n,Dial(SIP/1002&SIP/1003,30)  ; ring IP Phone 2 and softphone
 same => n,Hangup()
```

---

## File 4: `lab/scripts/seed-lab-data.sql`

```sql
-- ─────────────────────────────────────────
-- Lab seed data for local testing
-- Maps to your real galaxy_2 schema
-- (adjust table/column names to match Phase 0 audit)
-- ─────────────────────────────────────────

-- Test Account 1: Good credit
INSERT INTO accounts (id, name, token, status, balance_cents)
VALUES (
  'acc-test-001',
  'Test Account Alpha',
  'test-token-alpha-001',
  'active',
  100000  -- $1000.00 credit
);

-- Test Account 2: Low credit (will exhaust during test)
INSERT INTO accounts (id, name, token, status, balance_cents)
VALUES (
  'acc-test-002',
  'Test Account Beta',
  'test-token-beta-002',
  'active',
  120     -- $1.20 — enough for ~2 minutes at typical rate
);

-- Test Account 3: Zero credit (should be rejected immediately)
INSERT INTO accounts (id, name, token, status, balance_cents)
VALUES (
  'acc-test-003',
  'Test Account Zero',
  'test-token-zero-003',
  'active',
  0
);

-- Test Account 4: Suspended (should be rejected)
INSERT INTO accounts (id, name, token, status, balance_cents)
VALUES (
  'acc-test-004',
  'Test Account Suspended',
  'test-token-suspended-004',
  'suspended',
  50000
);

-- DID Mappings (fake Voxbone DIDs → accounts)
INSERT INTO dids (number, account_id, description)
VALUES
  ('+18001000001', 'acc-test-001', 'Test DID - Good Credit'),
  ('+18001000002', 'acc-test-002', 'Test DID - Low Credit'),
  ('+18001000003', 'acc-test-003', 'Test DID - Zero Credit'),
  ('+18001000004', 'acc-test-004', 'Test DID - Suspended');

-- Gateway (simulates outbound route via Asterisk)
INSERT INTO gateways (name, host, port, prefix, active)
VALUES (
  'asterisk-lab',
  '172.20.0.50',
  5060,
  '+',
  true
);

-- Rate card (cost per minute to outbound destinations)
INSERT INTO rate_cards (prefix, rate_per_minute, gateway_name)
VALUES
  ('+1',    0.010, 'asterisk-lab'),   -- US
  ('+44',   0.015, 'asterisk-lab'),   -- UK
  ('+94',   0.020, 'asterisk-lab'),   -- Sri Lanka
  ('+',     0.025, 'asterisk-lab');   -- Default/everywhere else

-- Seed Redis credit cache (run this after PostgreSQL seed)
-- Done via setup-lab.sh, not SQL
```

---

## File 5: `lab/scripts/setup-lab.sh`

```bash
#!/bin/bash
set -e

echo "════════════════════════════════════════"
echo "  VoIP Lab Setup"
echo "════════════════════════════════════════"

# 1. Create .env.lab if not exists
if [ ! -f .env.lab ]; then
  cp .env.lab.example .env.lab
  echo "✓ Created .env.lab — edit it before continuing"
fi

# 2. Start core services (no monitoring/loadtest yet)
docker compose -f docker-compose.lab.yml up -d postgres redis
echo "⏳ Waiting for PostgreSQL..."
sleep 5

# 3. Run DB migrations
docker compose -f docker-compose.lab.yml run --rm api \
  alembic upgrade head
echo "✓ DB migrations applied"

# 4. Seed Redis credit cache
docker exec voip-redis redis-cli SET "credit:acc-test-001" 100000
docker exec voip-redis redis-cli SET "credit:acc-test-002" 120
docker exec voip-redis redis-cli SET "credit:acc-test-003" 0
echo "✓ Redis credit cache seeded"

# 5. Start remaining services
docker compose -f docker-compose.lab.yml up -d
echo "✓ All lab services started"

# 6. Verify
echo ""
echo "════════════════════════════════════════"
echo "  Verification"
echo "════════════════════════════════════════"
sleep 3

# API health
curl -sf http://localhost:8000/health && echo "✓ FastAPI healthy" || echo "✗ FastAPI DOWN"

# FreeSWITCH status
fs_cli -x "status" 2>/dev/null | grep -q "UP" && echo "✓ FreeSWITCH running" || echo "⚠ FreeSWITCH — check manually"

# Asterisk status
docker exec voip-asterisk asterisk -rx "sip show peers" 2>/dev/null && echo "✓ Asterisk running" || echo "✗ Asterisk DOWN"

echo ""
echo "════════════════════════════════════════"
echo "  Lab Ready!"
echo "════════════════════════════════════════"
echo ""
echo "Register your IP phones:"
echo "  SIP Server:  $(hostname -I | awk '{print $1}')"
echo "  SIP Port:    5060"
echo "  Phone 1:     ext 1001  pass: 1001"
echo "  Phone 2:     ext 1002  pass: 1002"
echo ""
echo "Register softphones:"
echo "  Linphone:    ext 1003  pass: 1003"
echo "  Zoiper:      ext 1004  pass: 1004"
echo ""
echo "Test DIDs (dial from softphone via Asterisk):"
echo "  +18001000001  → Good credit account"
echo "  +18001000002  → Low credit (will cut at ~2min)"
echo "  +18001000003  → Zero credit (rejected)"
echo "  +18001000004  → Suspended account (rejected)"
echo ""
echo "Grafana:       http://localhost:3000  (with --profile monitoring)"
echo "Prometheus:    http://localhost:9090  (with --profile monitoring)"
```

---

## File 6: `lab/Makefile.lab`

```makefile
.PHONY: lab lab-monitor lab-load reset logs-fs logs-api fs-cli sipp-basic sipp-load sngrep

# Start core lab
lab:
	docker compose -f lab/docker-compose.lab.yml up -d
	@echo "Lab running. Run 'make fs-cli' to check FreeSWITCH."

# Start with monitoring
lab-monitor:
	docker compose -f lab/docker-compose.lab.yml --profile monitoring up -d

# Start with load testing
lab-load:
	docker compose -f lab/docker-compose.lab.yml --profile loadtest up -d

# Wipe everything and start fresh
reset:
	docker compose -f lab/docker-compose.lab.yml down -v
	bash lab/scripts/setup-lab.sh

# FreeSWITCH console
fs-cli:
	fs_cli

# FreeSWITCH show active calls
calls:
	fs_cli -x "show calls"

# Reload Lua scripts (no restart)
reload-lua:
	fs_cli -x "reload mod_lua"
	@echo "✓ Lua scripts reloaded"

# SIPp: single call test
sipp-basic:
	docker exec voip-sipp sipp 172.20.0.10 \
	  -sf /scenarios/basic_call.xml \
	  -l 1 -m 1 -r 1

# SIPp: 10 concurrent calls
sipp-concurrent:
	docker exec voip-sipp sipp 172.20.0.10 \
	  -sf /scenarios/concurrent_calls.xml \
	  -l 10 -m 10 -r 2

# SIP trace (install sngrep: apt install sngrep)
sngrep:
	sngrep -I any port 5060

# Redis: watch credit in real time
watch-credit:
	watch -n1 'redis-cli MGET credit:acc-test-001 credit:acc-test-002 credit:acc-test-003'

# DB: watch CDRs as they come in
watch-cdrs:
	watch -n2 'psql postgresql://dev_ifx:labpass@localhost:5432/galaxy_2 \
	  -c "SELECT call_uuid, disposition, billsec, cost_cents, created_at FROM cdr ORDER BY created_at DESC LIMIT 10;"'

# Logs
logs-fs:
	tail -f /var/log/freeswitch/freeswitch.log

logs-api:
	docker compose -f lab/docker-compose.lab.yml logs -f api

logs-billing:
	docker compose -f lab/docker-compose.lab.yml logs -f billing-worker

logs-asterisk:
	docker compose -f lab/docker-compose.lab.yml logs -f asterisk
```

---

## File 7: `lab/docs/lab-guide.md`

### IP Phone Registration
```
Server:    <your-machine-IP>
Port:      5060
Transport: UDP
Username:  1001  (or 1002)
Password:  1001  (or 1002)
Domain:    <your-machine-IP>
```

### Softphone Registration (Linphone / Zoiper)
```
SIP Proxy: <your-machine-IP>:5060
Username:  1003 (Linphone) / 1004 (Zoiper)
Password:  1003 / 1004
```

### Test Scenarios

#### Scenario 1 — Basic Call (Manual)
```
1. IP Phone 1 dials: +18001000001
   → Asterisk routes to FreeSWITCH
   → Lua calls FastAPI /authorize
   → FastAPI checks credit (100000 cents = plenty)
   → Call bridges to IP Phone 2 (ext 1002)
2. Answer on Phone 2
3. Talk for 30 seconds
4. Hang up
5. Check: make watch-cdrs  (CDR should appear)
6. Check: make watch-credit (credit should decrease)
```

#### Scenario 2 — Credit Exhaustion
```
1. Phone 1 dials: +18001000002 (120 cents = ~2 min)
2. Answer on Phone 2
3. Wait — at ~2 minutes you will hear "credit exhausted" prompt
4. Call automatically disconnects
5. Check CDR: disposition = ANSWERED, cost_cents ≈ 120
```

#### Scenario 3 — Rejected (Zero Credit)
```
1. Phone 1 dials: +18001000003
2. You immediately hear "insufficient credit" prompt
3. Call disconnects
4. Check CDR: disposition = FAILED, billsec = 0, cost = 0
```

#### Scenario 4 — SIPp Load Test
```
make sipp-concurrent
→ 10 simultaneous calls hit FreeSWITCH
→ Watch: make watch-credit
→ Watch: make calls  (FreeSWITCH active calls)
→ Watch: make logs-billing
```

### Debugging Tools

| Problem              | Command                          |
|----------------------|----------------------------------|
| SIP not registering  | `make sngrep`                    |
| One-way audio        | `tcpdump -i any port 16384:32768`|
| Lua errors           | `make logs-fs`                   |
| Auth failures        | `make logs-api`                  |
| CDR not written      | `make logs-billing`              |
| Credit not deducting | `make watch-credit`              |
| Active calls         | `make calls`                     |

---

## Constraints

- FreeSWITCH MUST use `network_mode: host` — never bridge for a lab with real IP phones
- Asterisk container uses bridge network — it communicates with FS via host IP
- IP phones register directly to FreeSWITCH (not through Asterisk)
- Asterisk only handles the "carrier/DID" simulation role
- All test account tokens must match what's in your real galaxy_2 schema format
- Adjust seed-lab-data.sql table/column names to match your Phase 0 schema-map.md
- sngrep and tcpdump installed on host machine (not in Docker)
