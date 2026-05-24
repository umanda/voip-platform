.PHONY: help dev dev-monitor dev-backend down down-all build build-api build-worker build-fs \
        logs logs-api logs-worker logs-fs ps shell-api shell-fs \
        test test-call test-billing test-coverage test-lua lint lint-fix \
        db-migrate db-migrate-dry db-rollback db-shell redis-cli redis-flush \
        reload-lua fs-cli fs-reload fs-reload-lua fs-sofia-status fs-calls \
        prod-config ecr-login

COMPOSE      := docker compose
COMPOSE_TEST := docker compose -f docker-compose.test.yml

# ── Help ──────────────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ── Development ───────────────────────────────────────────────────────────────

dev: .env ## Build and start all services (full stack including FreeSWITCH)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  API:           http://localhost:8000"
	@echo "  API docs:      http://localhost:8000/docs   (debug mode)"
	@echo "  Postgres:      localhost:5432"
	@echo "  Redis:         localhost:6379"
	@echo "  FreeSWITCH ESL: localhost:8021  (host network)"
	@echo ""
	@echo "  Run 'make logs' to tail logs."

dev-monitor: .env ## Start full stack + prometheus + grafana dashboards
	$(COMPOSE) --profile monitoring up --build -d
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3000  (admin / see GRAFANA_PASSWORD in .env)"

dev-backend: .env ## Start backend only (API + billing-worker + postgres + redis, no FreeSWITCH)
	$(COMPOSE) up --build -d api billing-worker postgres redis

down: ## Stop all services (keep volumes)
	$(COMPOSE) down
	$(COMPOSE_TEST) down 2>/dev/null || true

down-all: ## Stop all services and remove volumes (DESTRUCTIVE — deletes postgres + redis data)
	@echo "WARNING: This will delete all local data. Press Ctrl-C to cancel."
	@sleep 3
	$(COMPOSE) down -v --remove-orphans
	$(COMPOSE_TEST) down -v --remove-orphans 2>/dev/null || true

# ── Build ─────────────────────────────────────────────────────────────────────

build: ## Build production images (api + billing-worker). For FreeSWITCH: see build-fs
	docker build -t voip-api:local ./backend
	docker build -t voip-worker:local -f ./backend/Dockerfile.worker ./backend
	@echo ""
	@echo "  voip-api:local      built ✓"
	@echo "  voip-worker:local   built ✓"
	@echo ""
	@echo "  For FreeSWITCH image (local dev only, EC2 in prod):"
	@echo "    docker login -u YOUR_EMAIL docker.signalwire.com   # free account at id.signalwire.com"
	@echo "    make build-fs"

build-api: ## Build the FastAPI API image only
	docker build -t voip-api:local ./backend

build-worker: ## Build the billing worker image only
	docker build -t voip-worker:local -f ./backend/Dockerfile.worker ./backend

build-fs: ## Build the FreeSWITCH image (requires SignalWire login)
	@echo "Building FreeSWITCH image (requires docker.signalwire.com credentials)..."
	@echo "  Get a free account at: https://id.signalwire.com"
	@echo "  Login: docker login -u YOUR_EMAIL docker.signalwire.com"
	docker build -t voip-freeswitch:local ./freeswitch

# ── Logs ──────────────────────────────────────────────────────────────────────

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=50

logs-api: ## Tail API logs
	$(COMPOSE) logs -f --tail=50 api

logs-worker: ## Tail billing worker logs
	$(COMPOSE) logs -f --tail=50 billing-worker
logs-billing:
	$(COMPOSE) logs -f --tail=50 billing-worker  # alias

logs-fs: ## Tail FreeSWITCH logs
	$(COMPOSE) logs -f --tail=100 freeswitch

# ── Status ────────────────────────────────────────────────────────────────────

ps: ## Show service status and health
	$(COMPOSE) ps

# ── Shells ────────────────────────────────────────────────────────────────────

shell-api: ## Open a bash shell in the running API container
	$(COMPOSE) exec api bash

shell-worker: ## Open a bash shell in the billing worker container
	$(COMPOSE) exec billing-worker bash

shell-fs: ## Open a bash shell in the FreeSWITCH container
	$(COMPOSE) exec freeswitch bash

db-shell: ## Open a psql shell in the postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-dev_ifx} -d $${POSTGRES_DB:-galaxy_2}

# ── FreeSWITCH tools ──────────────────────────────────────────────────────────

fs-cli: ## Open fs_cli interactive console inside the FreeSWITCH container
	$(COMPOSE) exec freeswitch fs_cli

reload-lua: ## Hot-reload Lua scripts inside FreeSWITCH (no restart, no call disruption)
	docker exec -it voip-platform-freeswitch-1 fs_cli -x "reload mod_lua" 2>/dev/null \
		|| $(COMPOSE) exec freeswitch fs_cli -x "reload mod_lua"

fs-reload: ## Hot-reload FreeSWITCH XML config (no restart)
	$(COMPOSE) exec freeswitch fs_cli -x "reloadxml"

fs-reload-lua: ## Reload mod_lua scripts (hot, no call disruption)
	$(COMPOSE) exec freeswitch fs_cli -x "reload mod_lua"

fs-sofia-status: ## Show Sofia SIP profile and gateway status
	$(COMPOSE) exec freeswitch fs_cli -x "sofia status"

fs-calls: ## Show active calls
	$(COMPOSE) exec freeswitch fs_cli -x "show calls"

# ── Testing ───────────────────────────────────────────────────────────────────

test: ## Run unit tests only (no live server required)
	$(COMPOSE_TEST) run --rm api \
		pytest tests/ billing_worker/tests/ -v --tb=short -m "not integration"

test-integration: ## Run integration tests against live stack (requires make dev-backend first)
	$(COMPOSE_TEST) run --rm api \
		pytest tests/integration/ -v --tb=short -m integration

test-call: ## Run call authorization tests only
	$(COMPOSE_TEST) run --rm api \
		pytest tests/test_call_authorize.py -v

test-billing: ## Run billing/credit tests only
	$(COMPOSE_TEST) run --rm api \
		pytest tests/test_credit.py billing_worker/tests/ -v

test-coverage: ## Run unit tests with HTML coverage report (output: backend/htmlcov/)
	$(COMPOSE_TEST) run --rm api \
		pytest tests/ billing_worker/tests/ -m "not integration" \
		  --cov=app --cov=billing_worker \
		  --cov-report=html \
		  --cov-report=term-missing

test-lua: ## Run Lua unit tests inside the FreeSWITCH container
	$(COMPOSE) exec freeswitch lua /usr/share/freeswitch/scripts/lua/tests/test_auth.lua
	$(COMPOSE) exec freeswitch lua /usr/share/freeswitch/scripts/lua/tests/test_billing.lua

# ── Database ──────────────────────────────────────────────────────────────────

db-migrate: ## Run Alembic migrations against the local postgres container
	$(COMPOSE) run --rm api alembic upgrade head

db-migrate-dry: ## Show pending Alembic SQL without applying
	$(COMPOSE) run --rm api alembic upgrade head --sql

db-rollback: ## Rollback the last Alembic migration
	$(COMPOSE) run --rm api alembic downgrade -1

# ── Redis ──────────────────────────────────────────────────────────────────────

redis-cli: ## Open redis-cli in the Redis container
	$(COMPOSE) exec redis redis-cli

redis-flush: ## Flush all Redis keys (DESTRUCTIVE — dev only)
	@echo "WARNING: Flushing all Redis keys. Press Ctrl-C to cancel."
	@sleep 2
	$(COMPOSE) exec redis redis-cli FLUSHALL

# ── Linting ───────────────────────────────────────────────────────────────────

lint: ## Run ruff linter on all Python source
	ruff check backend/app/ backend/billing_worker/ backend/tests/

lint-fix: ## Run ruff with auto-fix
	ruff check --fix backend/app/ backend/billing_worker/ backend/tests/

# ── Production helpers ────────────────────────────────────────────────────────

prod-config: ## Print merged production docker-compose config
	docker compose -f docker-compose.yml -f docker-compose.prod.yml config

ecr-login: ## Login to AWS ECR (requires AWS CLI configured)
	aws ecr get-login-password --region $${AWS_REGION:-ap-southeast-1} \
		| docker login --username AWS --password-stdin \
		  $${ECR_REGISTRY}

# ── Guard: require .env ───────────────────────────────────────────────────────

.env:
	@echo "ERROR: .env file not found. Copy the template and fill in values:"
	@echo "  cp .env.example .env"
	@exit 1
