# Команды разработки: всё выполняется внутри Docker Compose
DC := docker compose

.PHONY: up down logs migrate seed shell test psql links

up:
	$(DC) up -d --build

down:
	$(DC) down

logs:
	$(DC) logs -f --tail=100 app

migrate:
	$(DC) exec app alembic upgrade head

seed:
	$(DC) exec app python -m seed.seed

links:
	$(DC) exec app python -m seed.seed --links

shell:
	$(DC) exec app bash

test:
	$(DC) exec app pytest -q

psql:
	$(DC) exec postgres psql -U korsik -d korsik
