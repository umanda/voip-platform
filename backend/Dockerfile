# syntax=docker/dockerfile:1
##
## Migration Notes:
##   Replaces PHP Sentinel on Apache. Runs FastAPI + Uvicorn.
##   Multi-stage: builder compiles C extensions; final image contains only runtime.
##   Non-root user: uid 1001 (voipapi) — no privilege escalation in ECS tasks.
##   HEALTHCHECK: polls GET /health — used by ECS container health + ALB target health.
##
## Build (from voip-platform/backend/):
##   docker build -t voip-api .
##
## ECS: set WORKERS via environment; default 2 is fine for Fargate 1 vCPU.

# ── Stage 1: builder — compile native extensions (asyncpg, hiredis) ─────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install into a prefix so we can copy cleanly into the final stage
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: final — minimal runtime image ──────────────────────────────────
FROM python:3.11-slim AS final

LABEL org.opencontainers.image.title="ifonix VoIP Platform API" \
      org.opencontainers.image.description="FastAPI backend replacing PHP Sentinel" \
      org.opencontainers.image.version="1.0.0"

# Runtime library for asyncpg (libpq5, not libpq-dev)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security — matches ECS task role boundary
RUN groupadd -r voipapi && useradd -r -g voipapi -u 1001 voipapi

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source; owned by non-root user
COPY --chown=voipapi:voipapi . .

USER voipapi

EXPOSE 8000

# Liveness probe — ECS marks task unhealthy if this fails 3× in 90s
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# FastAPI: CMD set here; billing worker overrides CMD in compose/ECS task def
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
