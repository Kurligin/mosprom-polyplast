"""Точка входа: API + фоновый бот (lifespan)."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.router import router as api_router
from app.config import settings
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("korsik")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task: asyncio.Task | None = None
    if settings.bot_enabled and settings.max_bot_token:
        try:
            from app.bot.runner import run_bot
        except Exception as e:  # noqa: BLE001 — пока модуля бота нет, API работает
            log.warning("Бот не запущен: модуль app.bot.runner недоступен (%s)", e)
        else:
            bot_task = asyncio.create_task(run_bot(), name="bot")
            log.info("Бот запущен")
    else:
        log.info("Бот выключен (BOT_ENABLED=false или нет MAX_BOT_TOKEN)")

    yield

    if bot_task:
        bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await bot_task
    await engine.dispose()


app = FastAPI(title="Корсик 2.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.error("health: БД недоступна: %s", e)
        return JSONResponse({"status": "error", "db": "down"}, status_code=503)
    return {"status": "ok", "db": "ok"}
