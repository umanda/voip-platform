# lab/asterisk/Dockerfile
# Asterisk 20 LTS — lightweight chan_sip for DID simulation only.
# Uses the official Debian-based image from MiRTA.
# Only chan_sip and basic PBX modules are needed; no WebRTC, no PJSIP.

FROM debian:bookworm-slim

# Install Asterisk 20 from Debian repos (20.x is in bookworm-backports)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        asterisk \
        asterisk-modules \
        asterisk-config \
        curl \
        net-tools && \
    rm -rf /var/lib/apt/lists/*

# Remove default config (we provide our own)
RUN rm -rf /etc/asterisk/*

# Our minimal config is mounted via volume in docker-compose.lab.yml
# (./asterisk/conf:/etc/asterisk)

EXPOSE 5060/udp
EXPOSE 5060/tcp

# Run as foreground, verbose logging to stdout
CMD ["asterisk", "-fp", "-C", "/etc/asterisk/asterisk.conf"]
