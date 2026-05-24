# lab/freeswitch-lab/Dockerfile
# FreeSWITCH 1.10 for LOCAL LAB use only (NOT for production).
#
# Uses SignalWire's official FreeSWITCH 1.10 packages on Debian bookworm.
# The SignalWire Personal Access Token is passed as a Docker build secret
# (--mount=type=secret) so it is NEVER baked into any image layer.
#
# Production FreeSWITCH runs on EC2 directly (not Docker).
# See freeswitch/Dockerfile for the production Docker image.
#
# IMPORTANT: This Dockerfile uses the PRODUCTION entrypoint unchanged so that
#   all env var names and config structure are identical.

# syntax=docker/dockerfile:1
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# Install prerequisites for adding the SignalWire apt repository
RUN apt-get update && apt-get install -y --no-install-recommends \
    gnupg2 \
    wget \
    lsb-release \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Add SignalWire FreeSWITCH repository.
# Token is passed as a build secret — never stored in a layer.
RUN --mount=type=secret,id=signalwire_token \
    TOKEN=$(cat /run/secrets/signalwire_token) && \
    wget --http-user=signalwire --http-password="${TOKEN}" \
         -O /usr/share/keyrings/signalwire-freeswitch-repo.gpg \
         https://freeswitch.signalwire.com/repo/deb/debian-release/signalwire-freeswitch-repo.gpg && \
    echo "machine freeswitch.signalwire.com login signalwire password ${TOKEN}" \
         > /etc/apt/auth.conf && \
    chmod 600 /etc/apt/auth.conf && \
    echo "deb [signed-by=/usr/share/keyrings/signalwire-freeswitch-repo.gpg] \
https://freeswitch.signalwire.com/repo/deb/debian-release/ $(lsb_release -sc) main" \
         > /etc/apt/sources.list.d/freeswitch.list && \
    apt-get update && apt-get install -y --no-install-recommends \
        freeswitch \
        freeswitch-mod-sofia \
        freeswitch-mod-lua \
        freeswitch-mod-dptools \
        freeswitch-mod-commands \
        freeswitch-mod-event-socket \
        freeswitch-mod-loopback \
        freeswitch-mod-tone-stream \
        freeswitch-mod-sndfile \
        freeswitch-mod-dialplan-xml \
        freeswitch-mod-console \
        freeswitch-mod-logfile \
        lua5.1 \
        liblua5.1-dev \
        lua-socket \
        lua-cjson \
        gettext-base \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/apt/auth.conf

# Ensure fs_cli is in PATH
RUN which fs_cli 2>/dev/null || ( \
    test -x /usr/bin/fs_cli && ln -s /usr/bin/fs_cli /usr/local/bin/fs_cli \
) || true

# FreeSWITCH installed via SignalWire packages but default config (freeswitch-conf-vanilla)
# is not installed — we mount our own config. Ensure required directories exist so the
# production entrypoint (docker-entrypoint.sh) can write event_socket.conf.xml.
RUN mkdir -p \
    /etc/freeswitch/autoload_configs \
    /etc/freeswitch/dialplan \
    /etc/freeswitch/directory \
    /etc/freeswitch/sip_profiles \
    /var/log/freeswitch \
    /var/run/freeswitch \
    && chown -R freeswitch:freeswitch /etc/freeswitch /var/log/freeswitch /var/run/freeswitch 2>/dev/null || true

# ── Lua scripts ───────────────────────────────────────────────────────────────
# Path must match lua.conf.xml: /usr/share/freeswitch/scripts/lua/
# In docker-compose.lab.yml these are bind-mounted from ../freeswitch/lua
# so they can be edited on the host without rebuilding.

# ── Entrypoint (identical to production) ─────────────────────────────────────
# Uses the PRODUCTION entrypoint so env var names and config generation
# are identical between lab and production.
# Build context = repo root so we can reference freeswitch/docker-entrypoint.sh
COPY freeswitch/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]

# Default SIP + ESL ports
EXPOSE 5060/udp
EXPOSE 5060/tcp
EXPOSE 5080/udp
EXPOSE 5080/tcp
EXPOSE 8021/tcp
