# lab/asterisk/Dockerfile
# Asterisk 18 LTS — lightweight chan_sip for DID simulation only.
# Uses Ubuntu 22.04 (universe repo has asterisk 18.x).
# Asterisk is NOT available in debian:bookworm apt repos.
# Only chan_sip and basic PBX modules are needed; no WebRTC, no PJSIP.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Enable universe repo (contains asterisk 18) then install
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common \
        gnupg && \
    add-apt-repository universe && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        asterisk \
        curl \
        net-tools && \
    rm -rf /var/lib/apt/lists/*

# Remove default config (we provide our own)
RUN rm -rf /etc/asterisk/*

# Create runtime directories Asterisk needs (not created by the package on Ubuntu)
RUN mkdir -p \
    /var/run/asterisk \
    /var/lib/asterisk/agi-bin \
    /var/log/asterisk/cdr-csv \
    && chown -R asterisk:asterisk /var/run/asterisk /var/lib/asterisk /var/log/asterisk

# Our minimal config is mounted via volume in docker-compose.lab.yml
# (./asterisk/conf:/etc/asterisk)

EXPOSE 5060/udp
EXPOSE 5060/tcp

# Run as foreground, verbose logging to stdout
CMD ["asterisk", "-fp", "-C", "/etc/asterisk/asterisk.conf"]
