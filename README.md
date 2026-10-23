# mosprom-polyplast

«Корсик 2.0» — ИИ-ассистент адаптации новых сотрудников в мессенджере «МАКС» для Группы ПОЛИПЛАСТИК. Хакатон «Открой Моспром», 2026.

**Главный документ — [plan.md](plan.md):** что нужно заказчику, кор-фичи, демо, бонусы, вопросы экспертам.

## Структура

| Путь | Что |
|---|---|
| [plan.md](plan.md) | Что делаем |
| [dev-plan.md](dev-plan.md) | План разработки: стек, архитектура, модули, дизайн, порядок сборки |
| [docs/case.md](docs/case.md) | Кейс: задачи, роли, требования, критерии оценки |
| [docs/ideas.md](docs/ideas.md) | Бэклог: все идеи со статусами |
| [docs/](docs/) | Материалы кейса в Markdown — база знаний бота |

## Запуск

Нужен Docker Desktop (на Apple Silicon образы собираются под arm64). Локально Python и Node не ставим.

```bash
cp .env.example .env        # заполнить токен бота «МАКС» и ключ GigaChat
docker compose up -d --build
make seed                   # демо-данные (все синтетические)
make links                  # ссылки привязки для тестовых новичков и персонала
```

- API: http://localhost:8000/health, Mini App (dev): http://localhost:5173, Postgres: `localhost:5433` (`korsik` / `korsik`).
- Миграции применяются при старте `app`; вручную — `make migrate`.
- Прочее: `make logs`, `make test`, `make psql`, `make shell`, `make down`.
- Чтобы не запускать long polling бота (если его уже опрашивает другой экземпляр), добавьте в `.env` строку `BOT_ENABLED=false`.
