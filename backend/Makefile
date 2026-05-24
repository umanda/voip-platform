.PHONY: dev down test logs ps install lint

dev:
	docker compose up --build -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f api

test:
	docker compose run --rm api pytest tests/ -v

install:
	pip install -r requirements.txt

lint:
	ruff check app/ billing_worker/ tests/
