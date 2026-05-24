# SIP Phone Setup & Real-Device Testing
> FreeSWITCH lab · Ubuntu 22.04 · Fanvil / Linphone / Zoiper / MicroSIP

---

## Architecture: Where Phones Register

Phones register to **FreeSWITCH internal profile on port 5060** (not Asterisk).  
Asterisk is the fake carrier — phones never talk to it directly.

```
Softphone / Hardphone (ext 1001–1005)
        │
        │  SIP REGISTER → <host-ip>:5060
        ▼
  FreeSWITCH internal profile (:5060)
        │
        │  Phone dials +18001000001
        │  dialplan/internal.xml → transfers to default context
        ▼
  dialplan/default.xml → lua/dialplan/auth.lua
        │
        │  IVR: "Enter 8-digit PIN"  ← DTMF from phone
        │  HTTP POST → FastAPI :8001 /v1/call/authorize
        │  FastAPI: checks credit in Redis → returns gateway + destination
        ▼
  FreeSWITCH bridges → Asterisk (asterisk-lab gateway :5080)
        │
        │  Asterisk [from-freeswitch] → routes ext 1002 back to FS :5060
        ▼
  Phone 2 (ext 1002) rings — answer and talk
```

---

## Find Your Host IP

```bash
ip route get 1 | awk '{print $7; exit}'
# Example output: 192.168.1.6  ← use this as SIP server address
```

---

## Extension Reference

| Ext  | Password | Role                              | Default Device |
|------|----------|-----------------------------------|----------------|
| 1001 | `1001`   | Caller — simulates PSTN customer  | Fanvil Phone 1 |
| 1002 | `1002`   | **Consultant** — receives calls   | Fanvil Phone 2 |
| 1003 | `1003`   | Extra softphone                   | Linphone       |
| 1004 | `1004`   | Extra softphone                   | Zoiper         |
| 1005 | `1005`   | Extra softphone                   | MicroSIP       |

Any extension can call any DID or ring ext 1002 — the roles above are just lab defaults.

---

## Test DIDs & PINs

| DID             | Credit Code | Credit    | Expected outcome           |
|-----------------|-------------|-----------|----------------------------|
| `+18001000001`  | `12341001`  | 1000 EUR  | Call connects to ext 1002  |
| `+18001000002`  | `12341002`  | 0.05 EUR  | Connects, hangs up ~2 min  |
| `+18001000003`  | `12341003`  | 0 EUR     | Rejected: INSUFFICIENT_CREDIT |
| `+18001000004`  | `12341004`  | blocked   | Rejected: ACCOUNT_SUSPENDED |

**Dial shorthand** — from an internal extension you can omit the leading `+`:
- `18001000001` — works
- `918001000001` — `9`-prefix alias also works (strips the `9`, adds `+`)

---

## Registration Settings (all phones)

| Parameter        | Value                  |
|------------------|------------------------|
| SIP Server       | `<host-ip>` (e.g. `192.168.1.6`) |
| SIP Port         | `5060`                 |
| Transport        | **UDP**                |
| Register Expiry  | `60` seconds           |
| DTMF Mode        | **RFC 2833** (not INFO, not inband) |
| Codec 1          | **G.711a — PCMA / alaw** |
| Codec 2          | G.711u — PCMU / ulaw   |
| Disable          | G.729, G.722, Opus, iLBC |

---

## Option A — Hardware IP Phone (Fanvil, Yealink, Snom, Cisco)

Settings are identical across vendors — field names vary slightly:

**Phone 1 (caller ext 1001):**

| Fanvil / Yealink field | Value          |
|------------------------|----------------|
| SIP Server / Registrar | `192.168.1.6`  |
| SIP Port               | `5060`         |
| Username / Account     | `1001`         |
| Authentication User    | `1001`         |
| Password               | `1001`         |
| Display Name           | `Lab Phone 1`  |

**Phone 2 (consultant ext 1002):** same settings, Username/Password = `1002`.

After saving, the phone should show **Registered** within a few seconds.  
If it shows "Trying" or "Failed" after 10 s, check the firewall section below.

---

## Option B — Linphone (Linux / macOS / Android / iOS)

**Desktop (GUI):**

1. Open Linphone → **Use SIP account**
2. Fill in:
   - Username: `1003`
   - Password: `1003`
   - SIP Domain: `192.168.1.6`
3. **Advanced** → Transport: **UDP** · Port: `5060`
4. **Preferences → Audio → Codecs** — enable only PCMA and PCMU, disable the rest
5. Green **Registered** indicator confirms success

**Desktop (CLI):**

```bash
# Install (Ubuntu)
sudo apt install linphone-cli

# Register
linphonecsh init
linphonecsh register --username 1003 --password 1003 --host 192.168.1.6

# Dial a DID
linphonecsh call sip:+18001000001@192.168.1.6

# Check registration status
linphonecsh status register

# Quit
linphonecsh quit
```

**Android / iOS:**

Same fields as desktop GUI above.  
Settings → Codecs: ensure PCMA is first, disable Opus/G.729.

---

## Option C — Zoiper (Windows / Android / iOS)

1. **Add account → SIP**
2. Username: `1004@192.168.1.6`  Password: `1004`
3. **Advanced** → Transport: `UDP` · Port: `5060`
4. **Preferences → Audio → Codecs** — move PCMA to top, disable G.729 / Opus / G.722
5. Confirm **green tick** next to the account

---

## Option D — MicroSIP (Windows, lightweight)

1. Menu → **Add account**
2. SIP Server: `192.168.1.6`  Username: `1005`  Password: `1005`
3. Transport: `UDP`
4. **Settings → Codecs** — enable PCMA + PCMU only

---

## Option E — Bria (Android / iOS)

1. New account → **SIP Account**
2. Domain: `192.168.1.6`  Username: `1003`  Password: `1003`
3. Proxy: `192.168.1.6:5060`  Transport: UDP
4. Advanced → DTMF: **RFC 2833**

---

## Making a Test Call (Step-by-Step)

**Prerequisite:** At least ext 1001 (or 1003–1005) and ext 1002 are registered.

**Step 1 — Open a credit-watch terminal (optional but useful):**

```bash
watch -n1 'docker exec voip-lab-redis redis-cli MGET credit:1 credit:2 credit:3'
```

**Step 2 — Dial from Phone 1:**

Dial `+18001000001` (or `18001000001` or `918001000001`)

**Step 3 — Enter PIN when IVR prompts:**

You will hear: *"Please enter your 8-digit PIN followed by hash"*

Press: `1 2 3 4 1 0 0 1 #`

> DTMF digits must be entered as individual key presses, not a paste.  
> The IVR times out after ~10 s of silence between digits.

**Step 4 — Phone 2 rings:**

Answer on ext 1002 and speak. Both sides should have clear audio.

**Step 5 — Hang up and verify:**

```bash
# CDR row in statistics table
docker exec voip-lab-postgres psql -U dev_ifx -d galaxy_2 -c \
  "SELECT unique_id, src_number, status, total_duration,
          credit_before, credit_after
   FROM statistics ORDER BY id DESC LIMIT 3;"

# Credit deducted in Redis
docker exec voip-lab-redis redis-cli GET credit:1
```

---

## Test Scenarios Reference

| Scenario               | Phone 1 dials      | PIN           | Ext 1002 rings? | Credit result        |
|------------------------|--------------------|---------------|-----------------|----------------------|
| Normal call            | `+18001000001`     | `12341001#`   | Yes             | Decreases each 60 s  |
| Low credit (~2 min)    | `+18001000002`     | `12341002#`   | Yes → auto-hang | Drains to 0          |
| Zero credit            | `+18001000003`     | `12341003#`   | No — rejected   | No change            |
| Blocked account        | `+18001000004`     | `12341004#`   | No — rejected   | No change            |
| Ext-to-ext (no auth)   | `1002`             | —             | Yes             | No credit used       |

---

## Resetting Credit Between Tests

```bash
docker exec voip-lab-redis redis-cli SET credit:1 100000000   # 1000 EUR Alpha
docker exec voip-lab-redis redis-cli SET credit:2 5000        # 0.05 EUR Beta
docker exec voip-lab-redis redis-cli SET credit:3 0           # Zero
```

---

## Verify FreeSWITCH Registration & Call State

```bash
# List all registered phones
docker exec voip-lab-freeswitch fs_cli -x "sofia status profile internal reg"

# Check Asterisk gateway is reachable
docker exec voip-lab-freeswitch fs_cli -x "sofia status gateway asterisk-lab"
# State should be: REACHABLE

# Show active calls
docker exec voip-lab-freeswitch fs_cli -x "show calls"

# Live SIP packet trace (run before dialing)
sudo sngrep -I any port 5060

# FreeSWITCH debug log (filter SIP events)
docker exec voip-lab-freeswitch fs_cli -x "console loglevel debug"
```

---

## Firewall Rules

If the phone is on a **different machine or device on the LAN**, open these ports on the Ubuntu host:

```bash
sudo ufw allow 5060/udp      # SIP signalling
sudo ufw allow 5060/tcp      # SIP over TCP (some phones)
sudo ufw allow 5080/udp      # FreeSWITCH external profile (Asterisk trunk)
sudo ufw allow 16384:32768/udp  # RTP audio media

sudo ufw reload
sudo ufw status
```

If the phone is on the **same machine** (localhost softphone), no firewall changes are needed.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| "Registration Failed" | Firewall blocking 5060/UDP | `sudo ufw allow 5060/udp` |
| "Trying" forever | Wrong server IP or phone on wrong VLAN | Verify `ip route get 1` and that phone can ping host |
| IVR answers but DTMF not recognised | Wrong DTMF mode | Set phone to **RFC 2833**, not SIP INFO or inband |
| One-way audio (you hear them, they can't hear you) | RTP ports blocked | `sudo ufw allow 16384:32768/udp` |
| No audio at all | NAT / STUN misconfiguration | Disable STUN in softphone settings — lab is LAN only |
| Phone 2 doesn't ring | `asterisk-lab` gateway unreachable | `fs_cli -x "sofia status gateway asterisk-lab"` — check REACHABLE |
| Call drops immediately after bridge | Asterisk can't reach FS :5060 | `docker exec voip-lab-asterisk asterisk -rx "sip show peers"` |
| "DID_NOT_FOUND" from API | Wrong DID dialed or seed not applied | Use exactly `+18001000001`–`+18001000004`; re-run `setup-lab.sh` |
| 500 Internal Server Error from API | ORM/DB mismatch | Confirm `consultant_statistics.py` columns match live `statistics` table |
| Credit not decreasing | Billing worker not connected to ESL | `docker logs voip-lab-billing-worker` — look for ESL connection errors |

---

## Related Documents

- [lab-guide.md](lab-guide.md) — Full lab setup, Docker architecture, SIPp load testing
