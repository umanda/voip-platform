# syntax=docker/dockerfile:1
##
## FreeSWITCH — LOCAL DEV / CI / STAGING IMAGE
##
## In PRODUCTION, FreeSWITCH runs directly on EC2 (not containerized).
## R-INFRA-01: EC2 must have an Elastic IP — Voxbone requires a static IP.
## See docs/guides/lab-setup-guide.md for production EC2 install steps.
##
## Base image options:
##   Option A (production / official): signalwire/freeswitch:1.10
##     Requires: docker login -u YOUR_EMAIL docker.signalwire.com
##     Uncomment the FROM below and comment out the Debian section.
##
##   Option B (local dev / CI — no auth needed): Debian Bookworm packages
##     FreeSWITCH 1.10.x is in the official Debian Bookworm apt repository.
##     This is the default used here.
##
## Switching to Option A:
##   docker login -u YOUR_EMAIL docker.signalwire.com
##   Uncomment: FROM signalwire/freeswitch:1.10 AS base
##   Comment out the debian build section below.
##
## NETWORK NOTE:
##   docker-compose.yml uses network_mode: host for FreeSWITCH.
##   Required for RTP NAT traversal in local dev on Linux.
##   Lua scripts reach the API at http://127.0.0.1:8000 (host loopback).

# ── Option A: SignalWire official image (requires auth) ──────────────────────
# FROM signalwire/freeswitch:1.10 AS base
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     lua5.1 liblua5.1-dev lua-socket lua-cjson gettext-base \
#     && rm -rf /var/lib/apt/lists/*

# ── Option B: Debian Bookworm packages (public, no auth) ─────────────────────
FROM debian:bookworm-slim AS base

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    # FreeSWITCH core + required modules
    freeswitch \
    freeswitch-mod-sofia \
    freeswitch-mod-lua \
    freeswitch-mod-dptools \
    freeswitch-mod-commands \
    freeswitch-mod-event-socket \
    freeswitch-mod-loopback \
    freeswitch-mod-tone-stream \
    freeswitch-mod-sndfile \
    # IVR prompts at 8kHz
    freeswitch-sounds-en-us-callie-8000 \
    # Standalone Lua for unit testing outside FreeSWITCH
    lua5.1 \
    liblua5.1-dev \
    lua-socket \
    lua-cjson \
    # envsubst for template-based config injection
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Ensure fs_cli is available
RUN which fs_cli || ln -sf /usr/bin/fs_cli /usr/local/bin/fs_cli 2>/dev/null || true

# ── Copy Lua scripts ──────────────────────────────────────────────────────────
# Path matches lua.conf.xml: /usr/share/freeswitch/scripts/lua/
COPY lua/ /usr/share/freeswitch/scripts/lua/

# ── Copy platform FreeSWITCH configuration ───────────────────────────────────
# vars.xml.tpl is the template; entrypoint generates vars.xml at startup
COPY conf/ /etc/freeswitch/

# ── Entrypoint: generates vars.xml from template, starts FreeSWITCH ──────────
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD fs_cli -x "status" | grep -q "UP" || exit 1

# SIP (UDP/TCP), TLS SIP, ESL
EXPOSE 5060/udp 5060/tcp 5061/tcp 8021/tcp
# RTP range — use network_mode: host for real media (R-INFRA-02)
EXPOSE 16384-32768/udp

CMD ["/docker-entrypoint.sh", "freeswitch", "-nobackground", "-nf"]
