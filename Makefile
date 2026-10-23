# Команды разработки: всё выполняется внутри Docker Compose
DC := docker compose

.PHONY: up down logs migrate seed shell test psql links import kb eval eval-flags tunnel

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
	$(DC) exec app python -m seed.surveys_seed

links:
	$(DC) exec app python -m seed.seed --links

shell:
	$(DC) exec app bash

test:
	$(DC) exec app pytest -q

psql:
	$(DC) exec postgres psql -U korsik -d korsik

# Сценарий из xlsx (data/raw/, не в git) → таблица scenario_steps
import:
	$(DC) exec app python -m app.scenario.store data/raw/korsik_scenario.xlsx

# Документы из docs/ и справочные статьи сценария → база знаний (с эмбеддингами)
kb:
	$(DC) exec app python -m app.kb.ingest --all

# Оценка качества на эталоне
eval:
	$(DC) exec app python -m eval.run --llm --write

# Точность и полнота флагов на синтетических ответах
eval-flags:
	$(DC) exec app python -m eval.flags.run

# HTTPS-туннель к Mini App, чтобы открыть её внутри «МАКС»; печатает адрес
tunnel:
	$(DC) --profile tunnel up -d tunnel
	@sleep 6; $(DC) logs tunnel 2>&1 | grep -o 'https://[a-z0-9]*\.lhr\.life' | tail -1

