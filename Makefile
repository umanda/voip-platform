.PHONY: help dev dev-backend down down-all build build-api build-fs \
        logs logs-api logs-worker logs-fs ps shell-api shell-fs \
        test test-backend test-lua lint db-migrate db-shell \
        redis-cli fs-cli fs-reload

COMPOSE        := docker compose
COMPOSE_PROD   := $(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml
COMPOSE_HOSTNET:= $(COMPOSE) -f docker-compose.yml -f docker-compose.host-net.yml

# ── Help ──────────────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Development ───────────────────────────────────────────────────────────────

dev: .env ## Build and start all services (full stack including FreeSWITCH)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  API:      http://localhost:$$(grep API_HOST_PORT .env | cut -d= -f2 || echo 8080)"
	@echo "  ESL:      localhost:8021"
	@echo "  Postgres: localhost:5432  (uncomment ports in docker-compose.yml to expose)"
	@echo "  Redis:    localhost:6379  (uncomment ports in docker-compose.yml to expose)"
	@echo ""
	@echo "  Run 'make logs' to tail logs, 'make ps' to check service health."

dev-backend: .env ## Start backend services only (API + worker + redis + postgres, no FreeSWITCH)
	$(COMPOSE) up --build -d api billing_worker redis postgres

dev-hostnet: .env ## Start full stack with FreeSWITCH on host network (Linux only — enables full RTP)
	$(COMPOSE_HOSTNET) up --build -d

down: ## Stop all services (keep volumes)
	$(COMPOSE) down

down-all: ## Stop all services and remove volumes (DESTRUCTIVE — deletes postgres data)
	@echo "WARNING: This will delete all local postgres data. Press Ctrl-C to cancel."
	@sleep 3
	$(COMPOSE) down -v --remove-orphans

# ── Build ─────────────────────────────────────────────────────────────────────

build: ## Build all images
	$(COMPOSE) build

build-api: ## Build the FastAPI image only
	$(COMPOSE) build api

build-worker: ## Build the billing worker image (same base as api)
	$(COMPOSE) build billing_worker

build-fs: ## Build the FreeSWITCH image (requires .signalwire_token)
	@if [ ! -f freeswitch/.signalwire_token ]; then \
		echo "ERROR: freeswitch/.signalwire_token not found."; \
		echo "  Get a free PAT from https://id.signalwire.com"; \
		echo "  echo 'YOUR_PAT' > freeswitch/.signalwire_token"; \
		exit 1; \
	fi
	DOCKER_BUILDKIT=1 $(COMPOSE) build freeswitch

# ── Logs ──────────────────────────────────────────────────────────────────────

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=50

logs-api: ## Tail API logs
	$(COMPOSE) logs -f --tail=50 api

logs-worker: ## Tail billing worker logs
	$(COMPOSE) logs -f --tail=50 billing_worker

logs-fs: ## Tail FreeSWITCH logs
	$(COMPOSE) logs -f --tail=100 freeswitch

# ── Status ────────────────────────────────────────────────────────────────────

ps: ## Show service status and health
	$(COMPOSE) ps

# ── Shells ────────────────────────────────────────────────────────────────────

shell-api: ## Open a shell in the running API container
	$(COMPOSE) exec api bash

shell-worker: ## Open a shell in the billing worker container
	$(COMPOSE) exec billing_worker bash

shell-fs: ## Open a shell in the FreeSWITCH container
	$(COMPOSE) exec freeswitch bash

# ── FreeSWITCH tools ──────────────────────────────────────────────────────────

fs-cli: ## Open fs_cli interactive console inside the FreeSWITCH container
	$(COMPOSE) exec freeswitch fs_cli

fs-reload: ## Hot-reload FreeSWITCH XML config (no restart, no call disruption)
	$(COMPOSE) exec freeswitch fs_cli -x "reloadxml"
	@echo "FreeSWITCH XML reloaded."

fs-reload-lua: ## Reload mod_lua (hot-reload Lua scripts, no call disruption)
	$(COMPOSE) exec freeswitch fs_cli -x "reload mod_lua"
	@echo "mod_lua reloaded."

fs-sofia-status: ## Show Sofia SIP profile and gateway status
	$(COMPOSE) exec freeswitch fs_cli -x "sofia status"

fs-calls: ## Show active calls
	$(COMPOSE) exec freeswitch fs_cli -x "show calls"

# ── Testing ───────────────────────────────────────────────────────────────────

test: ## Run the full Python test suite (pytest) inside the API container
	$(COMPOSE) run --rm api pytest tests/ billing_worker/tests/ -v --tb=short

test-call: ## Run call authorization tests only
	$(COMPOSE) run --rm api pytest tests/test_call_authorize.py -v

test-billing: ## Run billing/credit tests only
	$(COMPOSE) run --rm api pytest tests/test_credit.py billing_worker/tests/ -v

test-lua: ## Run Lua unit tests inside the FreeSWITCH container
	$(COMPOSE) exec freeswitch lua /usr/share/freeswitch/scripts/lua/tests/test_auth.lua
	$(COMPOSE) exec freeswitch lua /usr/share/freeswitch/scripts/lua/tests/test_billing.lua

# ── Database ──────────────────────────────────────────────────────────────────

db-migrate: ## Run Alembic migrations against the local postgres container
	$(COMPOSE) run --rm api alembic upgrade head

db-migrate-dry: ## Show pending Alembic migrations (dry run)
	$(COMPOSE) run --rm api alembic upgrade head --sql

db-rollback: ## Rollback the last Alembic migration
	$(COMPOSE) run --rm api alembic downgrade -1

db-shell: ## Open a psql shell in the local postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-dev_ifx} -d $${POSTGRES_DB:-galaxy_2}

# ── Redis ──────────────────────────────────────────────────────────────────────

redis-cli: ## Open redis-cli in the Redis container
	$(COMPOSE) exec redis redis-cli

redis-flush: ## Flush all Redis keys (DESTRUCTIVE — use only in dev)
	@echo "WARNING: Flushing all Redis keys. Press Ctrl-C to cancel."
	@sleep 2
	$(COMPOSE) exec redis redis-cli FLUSHALL

# ── Linting ───────────────────────────────────────────────────────────────────

lint: ## Run ruff linter on all Python source
	$(COMPOSE) run --rm api ruff check app/ billing_worker/ tests/

lint-fix: ## Run ruff with auto-fix
	$(COMPOSE) run --rm api ruff check --fix app/ billing_worker/ tests/

# ── Production helpers ────────────────────────────────────────────────────────

prod-config: ## Print the merged production docker-compose config (for ECS task def review)
	$(COMPOSE_PROD) config

ecr-login: ## Login to AWS ECR (requires AWS CLI configured)
	aws ecr get-login-password --region $${AWS_REGION:-ap-southeast-1} \
		| docker login --username AWS --password-stdin \
		  $${ECR_REGISTRY}

# ── Convenience ───────────────────────────────────────────────────────────────

.env:
	@echo "ERROR: .env file not found. Copy the template and fill in values:"
	@echo "  cp .env.example .env"
	@exit 1
