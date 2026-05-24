# syntax=docker/dockerfile:1.4
##
## FreeSWITCH — LOCAL DEV / CI / STAGING IMAGE
##
## In PRODUCTION, FreeSWITCH runs directly on EC2 (not containerized).
## R-INFRA-01: EC2 must have an Elastic IP — Voxbone requires a static IP.
## See docs/guides/lab-setup-guide.md for production EC2 install steps.
##
## This image is used for:
##   - Local full-stack call-flow testing
##   - CI integration tests (ESL + billing worker)
##   - Staging environment on EC2 (with host networking)
##
## BUILD REQUIREMENTS:
##   A free SignalWire Personal Access Token (PAT) is required to pull
##   the FreeSWITCH Debian packages. Get one at:
##     https://id.signalwire.com → API Credentials → Create a new PAT
##
##   Build with BuildKit secret (token never baked into any layer):
##     DOCKER_BUILDKIT=1 docker build \
##       --secret id=signalwire_token,src=.signalwire_token \
##       -t voip-freeswitch .
##
##   Or via docker compose:
##     echo "PAT_TOKEN" > .signalwire_token
##     docker compose build freeswitch
##
## NETWORK NOTE:
##   SIP/RTP requires either:
##     - network_mode: host  (Linux — recommended for real SIP traffic)
##     - Explicit port mapping of 5060/udp + RTP range 16384-32768/udp
##   The docker-compose.yml uses bridge network; RTP media won't traverse
##   NAT without STUN/TURN. For full call testing, override to host network.

FROM debian:bookworm-slim AS base

ARG DEBIAN_FRONTEND=noninteractive

# ── Step 1: Install build prerequisites ──────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gnupg2 \
    wget \
    ca-certificates \
    lsb-release \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

# ── Step 2: Add SignalWire FreeSWITCH repo (token via BuildKit secret) ───────
# Secret never written to an image layer — uses tmpfs at build time.
RUN --mount=type=secret,id=signalwire_token \
    TOKEN=$(cat /run/secrets/signalwire_token) \
    && wget -q --http-user=signalwire --http-password="${TOKEN}" \
       -O /usr/share/keyrings/signalwire-freeswitch-repo.gpg \
       "https://freeswitch.signalwire.com/repo/deb/debian-release/bookworm/signalwire-freeswitch-repo.gpg" \
    && echo "machine freeswitch.signalwire.com login signalwire password ${TOKEN}" \
       > /etc/apt/auth.conf \
    && echo "deb [signed-by=/usr/share/keyrings/signalwire-freeswitch-repo.gpg] \
       https://freeswitch.signalwire.com/repo/deb/debian-release/ bookworm main" \
       > /etc/apt/sources.list.d/freeswitch.list \
    && apt-get update \
    # Install core + modules used by this platform:
    #   mod_sofia    — SIP stack (inbound Voxbone + outbound gateway)
    #   mod_lua      — Dialplan scripting (replaces mod_perl)
    #   mod_dptools  — bridge, playback, set, hangup actions
    #   mod_commands — fs_cli RPC support
    #   mod_event_socket — ESL for billing worker
    #   mod_loopback — internal call routing (used in IVR tests)
    #   freeswitch-sounds-en-us-callie-8000 — IVR prompts (8kHz)
    && apt-get install -y --no-install-recommends \
       freeswitch \
       freeswitch-mod-sofia \
       freeswitch-mod-lua \
       freeswitch-mod-dptools \
       freeswitch-mod-commands \
       freeswitch-mod-event-socket \
       freeswitch-mod-loopback \
       freeswitch-mod-tone-stream \
       freeswitch-mod-sndfile \
       freeswitch-sounds-en-us-callie-8000 \
    # Clean up apt auth credentials — never leave them in the final layer
    && rm -f /etc/apt/auth.conf \
    && rm -rf /var/lib/apt/lists/*

# ── Step 3: Copy Lua scripts (dialplan + billing + lib) ──────────────────────
# Mounted at the path configured in lua.conf.xml: /usr/share/freeswitch/scripts/lua/
COPY lua/ /usr/share/freeswitch/scripts/lua/

# ── Step 4: Copy platform FreeSWITCH configuration ───────────────────────────
# Overrides default FreeSWITCH conf at /etc/freeswitch/
# vars.xml.tpl is the template; entrypoint generates vars.xml from it.
COPY conf/ /etc/freeswitch/

# ── Step 5: Copy and enable entrypoint ───────────────────────────────────────
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# ── Ports ─────────────────────────────────────────────────────────────────────
# 5060/udp+tcp  SIP signalling (inbound Voxbone + outbound calls)
# 5080/udp+tcp  SIP alternate profile (optional)
# 8021/tcp      ESL — billing worker subscribes here
# 16384-32768   RTP media — must be open bidirectionally (R-INFRA-02)
EXPOSE 5060/udp 5060/tcp 5080/udp 5080/tcp 8021/tcp

# ── Health check ──────────────────────────────────────────────────────────────
# fs_cli -x "status" returns non-zero if FreeSWITCH is not ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD fs_cli -x "status" 2>/dev/null | grep -q "^UP" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
