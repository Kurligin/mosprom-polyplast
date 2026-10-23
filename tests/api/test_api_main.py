"""Точка входа (app/main.py): запуск и остановка (lifespan), планировщик задач, /health.

Бот, браузер сводки и движок БД подменены: тест не ходит в «МАКС» и не закрывает общий пул соединений.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.bot.runner as bot_runner
from app import jobs, main, plugins
from app.config import settings
from app.reports import render

pytestmark = pytest.mark.asyncio


class FakeEngine:
    def __init__(self):
        self.disposed = 0

    async def dispose(self):
        self.disposed += 1


class FakeScheduler:
    def __init__(self):
        self.shutdowns: list[bool] = []

    def shutdown(self, wait=True):
        self.shutdowns.append(wait)


@pytest.fixture
def env(monkeypatch):
    """Окружение lifespan без побочных эффектов: модули, браузер сводки, пул БД — фейки."""
    state = {"plugins": 0, "render_closed": 0, "engine": FakeEngine()}

    def fake_plugins():
        state["plugins"] += 1
        return ["app.test.hooks"]

    async def fake_close():
        state["render_closed"] += 1

    monkeypatch.setattr(plugins, "load_plugins", fake_plugins)
    monkeypatch.setattr(render, "close", fake_close)
    monkeypatch.setattr(main, "engine", state["engine"])
    monkeypatch.setattr(settings, "max_bot_token", "TOKEN")
    monkeypatch.setattr(settings, "bot_enabled", False)
    monkeypatch.setattr(settings, "jobs_enabled", False)
    return state


@pytest.fixture
def no_jobs(monkeypatch):
    monkeypatch.setattr(jobs, "_JOBS", {})


# --- lifespan ---------------------------------------------------------------------------------


async def test_lifespan_bot_and_jobs_disabled(env, monkeypatch, caplog):
    called = []
    monkeypatch.setattr(bot_runner, "run_bot", lambda: called.append("run_bot"))
    with caplog.at_level(logging.INFO, logger="korsik"):
        async with main.lifespan(main.app):
            assert env["plugins"] == 1
    assert called == [], "BOT_ENABLED=false — бот не стартует"
    assert "JOBS_ENABLED=false" in caplog.text and "BOT_ENABLED=false" in caplog.text
    assert env["render_closed"] == 1 and env["engine"].disposed == 1


async def test_lifespan_without_token_does_not_start_bot(env, monkeypatch):
    monkeypatch.setattr(settings, "bot_enabled", True)
    monkeypatch.setattr(settings, "max_bot_token", "")
    called = []
    monkeypatch.setattr(bot_runner, "run_bot", lambda: called.append("run_bot"))
    async with main.lifespan(main.app):
        pass
    assert called == []


async def test_lifespan_starts_and_stops_bot_and_scheduler(env, monkeypatch):
    monkeypatch.setattr(settings, "bot_enabled", True)
    scheduler = FakeScheduler()
    monkeypatch.setattr(main, "_start_scheduler", lambda: scheduler)
    started, cancelled = asyncio.Event(), []

    async def fake_run_bot():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(bot_runner, "run_bot", fake_run_bot)
    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), 5)
        assert not cancelled
    assert cancelled == [True], "при остановке задача бота отменяется и дожидается"
    assert scheduler.shutdowns == [False]
    assert env["render_closed"] == 1 and env["engine"].disposed == 1


async def test_lifespan_survives_crashed_bot_and_render_error(env, monkeypatch):
    monkeypatch.setattr(settings, "bot_enabled", True)

    async def crashed():
        raise RuntimeError("бот упал")

    async def broken_close():
        raise RuntimeError("браузера нет")

    monkeypatch.setattr(bot_runner, "run_bot", crashed)
    monkeypatch.setattr(render, "close", broken_close)
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
    assert env["engine"].disposed == 1, "остановка дошла до конца"


async def test_lifespan_api_works_without_bot_module(env, monkeypatch, caplog):
    monkeypatch.setattr(settings, "bot_enabled", True)
    monkeypatch.setitem(sys.modules, "app.bot.runner", None)  # import падает
    with caplog.at_level(logging.WARNING, logger="korsik"):
        async with main.lifespan(main.app):
            pass
    assert "app.bot.runner недоступен" in caplog.text
    assert env["engine"].disposed == 1


# --- планировщик ------------------------------------------------------------------------------


async def test_scheduler_disabled(monkeypatch):
    monkeypatch.setattr(settings, "jobs_enabled", False)
    monkeypatch.setattr(jobs, "_JOBS", {"x": jobs.Job("x", lambda: None, seconds=1)})
    assert main._start_scheduler() is None


async def test_scheduler_without_jobs(monkeypatch, no_jobs):
    monkeypatch.setattr(settings, "jobs_enabled", True)
    assert main._start_scheduler() is None


async def test_scheduler_interval_and_cron_jobs(monkeypatch, no_jobs):
    monkeypatch.setattr(settings, "jobs_enabled", True)

    @jobs.job("test_tick", seconds=60)
    async def tick() -> None: ...

    @jobs.job("test_weekly", cron={"day_of_week": "mon", "hour": 9})
    async def weekly() -> None: ...

    scheduler = main._start_scheduler()
    try:
        assert scheduler.running
        by_id = {j.id: j for j in scheduler.get_jobs()}
        assert set(by_id) == {"test_tick", "test_weekly"}
        t, w = by_id["test_tick"], by_id["test_weekly"]
        assert t.func is tick and isinstance(t.trigger, IntervalTrigger)
        assert t.trigger.interval == timedelta(seconds=60)
        assert w.func is weekly and isinstance(w.trigger, CronTrigger)
        assert str(w.trigger.timezone) == "Europe/Moscow", "время cron — московское"
        fields = {f.name: str(f) for f in w.trigger.fields}
        assert fields["day_of_week"] == "mon" and fields["hour"] == "9" and fields["minute"] == "0"
        for j in (t, w):
            assert (j.max_instances, j.coalesce, j.misfire_grace_time) == (1, True, 60), "задача не копится"
    finally:
        scheduler.shutdown(wait=False)


async def test_job_needs_exactly_one_schedule():
    with pytest.raises(ValueError):
        jobs.job("x")
    with pytest.raises(ValueError):
        jobs.job("x", seconds=1, cron={"hour": 9})


# --- /health и лимит тела -----------------------------------------------------------------------


async def _get_health():
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as c:
        return await c.get("/health")


async def test_health_db_ok(monkeypatch):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    monkeypatch.setattr(main, "engine", engine)
    try:
        r = await _get_health()
    finally:
        await engine.dispose()
    assert r.status_code == 200 and r.json() == {"status": "ok", "db": "ok"}


async def test_health_db_down(monkeypatch, caplog):
    url = make_url(settings.database_url).set(host="127.0.0.1", port=1)  # порт закрыт: соединение не установится
    engine = create_async_engine(url, poolclass=NullPool)
    monkeypatch.setattr(main, "engine", engine)
    try:
        with caplog.at_level(logging.ERROR, logger="korsik"):
            r = await _get_health()
    finally:
        await engine.dispose()
    assert r.status_code == 503 and r.json() == {"status": "error", "db": "down"}
    assert "БД недоступна" in caplog.text
    assert settings.database_url.split("@")[0] not in caplog.text, "пароль БД в лог не попадает"
