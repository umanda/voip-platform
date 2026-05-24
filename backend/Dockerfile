# syntax=docker/dockerfile:1
##
## Migration Notes:
##   Replaces PHP Sentinel on Apache. Runs FastAPI + Uvicorn.
##   Multi-stage: builder compiles C extensions; final image contains only runtime.
##   Non-root user: uid 1001 (voipapi) — no privilege escalation in ECS tasks.
##   HEALTHCHECK: polls GET /health — used by ECS container health + ALB target health.
##
## Build (from voip-platform/backend/):
##   docker build -t voip-api:local .
##
## ECS: UVICORN_WORKERS env var; default 2 is fine for Fargate 1 vCPU.

# ── Stage 1: builder — compile native extensions (asyncpg, hiredis) ─────────
# Alpine base: ~50 MB, significantly smaller than slim (~130 MB)
FROM python:3.11-alpine AS builder

WORKDIR /app

# Build dependencies for asyncpg (C extension) and hiredis
COPY requirements.prod.txt .
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libpq-dev \
    linux-headers \
    && pip install --no-cache-dir --prefix=/install -r requirements.prod.txt

# ── Stage 2: runtime — minimal production image ──────────────────────────────
FROM python:3.11-alpine AS runtime

LABEL org.opencontainers.image.title="VoIP Platform API" \
      org.opencontainers.image.description="FastAPI backend replacing PHP Sentinel" \
      org.opencontainers.image.version="1.0.0"

WORKDIR /app

# Runtime library for asyncpg (libpq-compat) + curl for HEALTHCHECK
# Alpine packages are much smaller than Debian equivalents
RUN apk add --no-cache \
    libpq \
    curl

# Non-root user for security — matches ECS task role boundary
RUN addgroup -S voipapi && adduser -S -G voipapi -u 1001 voipapi

# Copy installed packages from builder stage — system-wide, accessible to any user
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=voipapi:voipapi . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER voipapi

EXPOSE 8000

# Liveness probe — ECS marks task unhealthy if this fails 3× in 90s
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# FastAPI: CMD set here; billing worker overrides CMD in compose/ECS task def
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop", "--access-log"]

# ── Stage 3: test — extends runtime with test packages ───────────────────────
# Used by docker-compose.test.yml (target: test) to run pytest in isolation.
# Never deployed to production.
FROM runtime AS test

USER root
COPY requirements.txt .
# Install test-only packages (pytest, httpx, etc.) on top of production packages
RUN apk add --no-cache gcc musl-dev libpq-dev linux-headers \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del gcc musl-dev libpq-dev linux-headers

USER voipapi
CMD ["pytest", "tests/", "billing_worker/tests/", "-v", "--tb=short"]
