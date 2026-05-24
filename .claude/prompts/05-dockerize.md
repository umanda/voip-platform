# Prompt: Dockerize All Components (Phase 5)

## Prerequisites
- Phases 1–4 complete and tested
- All components working in local development

## Task

Containerize every component. The result must be a fully runnable
`docker-compose up` for local development and isolated Dockerfiles
for production deployment on AWS ECS / EC2.

## Required Files

### 1. `freeswitch/Dockerfile`

```dockerfile
# FreeSWITCH production container
# Based on official SignalWire FreeSWITCH image
FROM signalwire/freeswitch:1.10 AS base

# Install Lua dependencies
RUN apt-get update && apt-get install -y \
    lua5.1 \
    liblua5.1-dev \
    lua-socket \
    lua-cjson \
    && rm -rf /var/lib/apt/lists/*

# Copy configuration
COPY conf/ /etc/freeswitch/
COPY lua/ /usr/share/freeswitch/scripts/

# Copy sound files (IVR prompts)
COPY sounds/ /usr/share/freeswitch/sounds/voip/

# Health check via ESL
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD fs_cli -x "status" | grep -q "UP" || exit 1

EXPOSE 5060/udp 5060/tcp 5061/tcp 8021/tcp
# RTP range — must match Security Group
EXPOSE 16384-32768/udp

CMD ["/docker-entrypoint.sh", "freeswitch", "-nobackground", "-nf"]
```

### 2. `backend/Dockerfile`

```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim AS runtime
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY app/ ./app/

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop", "--access-log"]
```

### 3. `backend/Dockerfile.worker`

```dockerfile
FROM python:3.11-slim AS runtime
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY billing_worker/ ./billing_worker/
COPY app/core/ ./app/core/
COPY app/models/ ./app/models/

ENV PYTHONUNBUFFERED=1

# No health check port — worker doesn't listen
# ECS will detect unhealthy worker via CloudWatch alarm on RunningTaskCount

CMD ["python", "-m", "billing_worker.worker"]
```

### 4. `docker-compose.yml` (local development)

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: galaxy_2
      POSTGRES_USER: dev_ifx
      POSTGRES_PASSWORD: dev_password_local
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/db/init.sql:/docker-entrypoint-initdb.d/init.sql

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  freeswitch:
    build:
      context: ./freeswitch
      dockerfile: Dockerfile
    ports:
      - "5060:5060/udp"
      - "5060:5060/tcp"
      - "8021:8021"
    environment:
      - API_BASE_URL=http://api:8000
      - INTERNAL_TOKEN=${INTERNAL_TOKEN}
    depends_on:
      - api
    network_mode: host  # Required for RTP NAT traversal in local dev
    volumes:
      - ./freeswitch/lua:/usr/share/freeswitch/scripts  # live reload in dev
      - ./freeswitch/conf:/etc/freeswitch

  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://dev_ifx:dev_password_local@postgres:5432/galaxy_2
      - REDIS_URL=redis://redis:6379
      - INTERNAL_JWT_SECRET=${INTERNAL_JWT_SECRET}
      - ENVIRONMENT=development
    depends_on:
      - postgres
      - redis
    volumes:
      - ./backend/app:/app/app  # hot reload in dev
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  billing-worker:
    build:
      context: ./backend
      dockerfile: Dockerfile.worker
    environment:
      - DATABASE_URL=postgresql+asyncpg://dev_ifx:dev_password_local@postgres:5432/galaxy_2
      - REDIS_URL=redis://redis:6379
      - FREESWITCH_ESL_HOST=freeswitch
      - FREESWITCH_ESL_PORT=8021
      - FREESWITCH_ESL_PASSWORD=${FS_ESL_PASSWORD}
    depends_on:
      - postgres
      - redis
      - freeswitch

  # Optional: local monitoring
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    profiles: ["monitoring"]

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
    profiles: ["monitoring"]

volumes:
  postgres_data:
  redis_data:
  grafana_data:
```

### 5. `.env.example` (committed to repo — actual `.env` gitignored)

```bash
# Local development only — production uses AWS Secrets Manager

# Database
DATABASE_URL=postgresql+asyncpg://dev_ifx:dev_password_local@localhost:5432/galaxy_2

# Redis
REDIS_URL=redis://localhost:6379

# FreeSWITCH
FREESWITCH_ESL_HOST=localhost
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=ClueCon

# Internal service auth
INTERNAL_JWT_SECRET=change_me_in_production_min_32_chars

# API
API_BASE_URL=http://localhost:8000

# AWS (for local development with AWS services)
AWS_REGION=ap-southeast-1
AWS_PROFILE=voip-dev
```

### 6. `scripts/db/init.sql`

Initialize the local development database with the schema from Phase 0 audit.
Include sample data for testing:
- 2 test accounts with credit
- 2 test DIDs
- 1 test gateway
- Sample rate card entries

### 7. `Makefile` (developer UX)

```makefile
.PHONY: dev test lint build

dev:
	docker-compose up

dev-monitor:
	docker-compose --profile monitoring up

test:
	docker-compose -f docker-compose.test.yml run --rm api pytest tests/ -v

test-coverage:
	docker-compose -f docker-compose.test.yml run --rm api \
		pytest tests/ --cov=app --cov-report=html

lint:
	ruff check backend/
	mypy backend/app/

build:
	docker build -t voip-api:local ./backend
	docker build -t voip-worker:local -f ./backend/Dockerfile.worker ./backend
	docker build -t voip-freeswitch:local ./freeswitch

reload-lua:
	docker exec -it voip-platform-freeswitch-1 fs_cli -x "reload mod_lua"

fs-cli:
	docker exec -it voip-platform-freeswitch-1 fs_cli

logs-billing:
	docker-compose logs -f billing-worker

logs-api:
	docker-compose logs -f api

db-shell:
	docker exec -it voip-platform-postgres-1 psql -U dev_ifx -d galaxy_2
```

## Integration Tests

Create `tests/integration/test_call_flow.py`:
Using `sipp` or a SIP test library to:
1. Send fake SIP INVITE to FreeSWITCH
2. Verify Lua calls FastAPI /authorize
3. Verify call is bridged
4. Wait 65 seconds
5. Verify billing tick was called
6. Send SIP BYE
7. Verify CDR written to PostgreSQL
8. Verify Redis credit was decremented

## Constraints
- Dockerfile must use multi-stage builds (no dev deps in production image)
- Images must have health checks
- Never use `latest` tag for base images — pin specific versions
- `network_mode: host` only for FreeSWITCH in local dev (RTP NAT workaround)
- In production, FreeSWITCH runs on EC2 directly (not Docker) — note this
- All secrets from environment variables (injected by docker-compose or ECS)
