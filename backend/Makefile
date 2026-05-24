.PHONY: dev down test logs ps install lint lint-fix

# Backend-only compose (no FreeSWITCH).
# For the full stack, use the root Makefile: cd .. && make dev
COMPOSE := docker compose

dev:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f api

logs-worker:
	$(COMPOSE) logs -f billing_worker

test:
	$(COMPOSE) run --rm api pytest tests/ billing_worker/tests/ -v --tb=short

install:
	pip install -r requirements.txt

lint:
	ruff check app/ billing_worker/ tests/

lint-fix:
	ruff check --fix app/ billing_worker/ tests/

db-migrate:
	$(COMPOSE) run --rm api alembic upgrade head

shell:
	$(COMPOSE) exec api bash
