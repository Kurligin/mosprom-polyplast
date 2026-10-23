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


def _start_scheduler():
    """Фоновые задачи модулей (app.jobs) в APScheduler. None — задач нет или они выключены."""
    if not settings.jobs_enabled:
        log.info("Фоновые задачи выключены (JOBS_ENABLED=false)")
        return None
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from app.jobs import registered

    jobs = registered()
    if not jobs:
        return None
    scheduler = AsyncIOScheduler(timezone=settings.tz_default)
    for j in jobs:
        trigger = IntervalTrigger(seconds=j.seconds) if j.seconds else CronTrigger(timezone=settings.tz_default,
                                                                                   **j.cron)
        scheduler.add_job(j.func, trigger, id=j.name, max_instances=1, coalesce=True, misfire_grace_time=60)
    scheduler.start()
    log.info("Планировщик: %s", ", ".join(j.name for j in jobs))
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.plugins import load_plugins

    log.info("Модули: %s", ", ".join(load_plugins()) or "нет")
    scheduler = _start_scheduler()
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

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    if bot_task:
        bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await bot_task
    with contextlib.suppress(Exception):
        from app.reports import render

        await render.close()  # браузер Playwright для картинки сводки
    await engine.dispose()


app = FastAPI(title="Корсик 2.0", lifespan=lifespan)
# Лимит тела запроса (загрузки): 413 до чтения файла целиком
from app.api.deps import BodySizeLimit

app.add_middleware(BodySizeLimit)
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
