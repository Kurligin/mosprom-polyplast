"""Цикл бота (app/bot/runner.py): транзакция на событие, маркер /updates, повтор после сбоя, остановка.

Сеть не трогаем: MaxApi подменён фейком. Маркер пишется в тестовую сессию (транзакция с откатом),
а не в живую БД: иначе тест сдвинул бы маркер настоящего бота.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

import httpx
import pytest

import app.db.session as db_session
from app.bot import runner, runtime
from app.bot.api import UPDATE_TYPES, MaxApiError
from app.bot.repo import SqlRepo
from app.bot.texts import COMMANDS
from app.config import settings
from app.db.models import Setting

pytestmark = pytest.mark.asyncio

KEY = "runner_test_key"


class _Factory:
    """async_session() → тестовая сессия; закрывать её не нужно: откатит фикстура."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


class _Broken:
    def __call__(self):
        return self

    async def __aenter__(self):
        raise OSError("БД недоступна")

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def test_db(session, monkeypatch):
    monkeypatch.setattr(db_session, "async_session", _Factory(session))
    # свой ключ маркера: строку живого бота не читаем и не блокируем
    monkeypatch.setattr(runner, "MARKER_KEY", "max_updates_marker_test")
    return session


# --- одно событие — одна транзакция ---------------------------------------------------------


async def test_process_update_commits_handler_changes(test_db, monkeypatch):
    seen = []

    async def handler(ctx, repo, upd):
        seen.append((ctx, type(repo), repo.s is test_db, upd))
        await repo.set_setting(KEY, "записано")

    monkeypatch.setattr(runner, "handle_update", handler)
    ctx = object()
    await runner.process_update(ctx, {"update_type": "message_created"})
    assert seen == [(ctx, SqlRepo, True, {"update_type": "message_created"})]
    test_db.expunge_all()
    assert (await test_db.get(Setting, KEY)).value == "записано"


async def test_process_update_rolls_back_on_error(test_db, monkeypatch):
    async def handler(ctx, repo, upd):
        await repo.set_setting(KEY, "полдела")
        raise RuntimeError("обработчик упал")

    monkeypatch.setattr(runner, "handle_update", handler)
    with pytest.raises(RuntimeError, match="обработчик упал"):
        await runner.process_update(None, {"update_type": "message_created"})
    test_db.expunge_all()
    assert await test_db.get(Setting, KEY) is None, "полузаписанное событие откатывается"


# --- маркер /updates ----------------------------------------------------------------------------


async def test_marker_save_and_load(test_db):
    assert await runner._load_marker() is None
    await runner._save_marker(41)
    await runner._save_marker(42)
    assert await runner._load_marker() == 42
    assert (await test_db.get(Setting, runner.MARKER_KEY)).value == 42


async def test_marker_db_errors_do_not_stop_bot(monkeypatch, caplog):
    monkeypatch.setattr(db_session, "async_session", _Broken())
    with caplog.at_level(logging.WARNING, logger="korsik.bot"):
        assert await runner._load_marker() is None
        await runner._save_marker(7)  # не бросает
    assert "прочитать маркер" in caplog.text and "сохранить маркер" in caplog.text


async def test_marker_garbage_is_ignored(test_db):
    await SqlRepo(test_db).set_setting(runner.MARKER_KEY, "не число")
    assert await runner._load_marker() is None


# --- старт: подписки, меню, /me -----------------------------------------------------------------


class FakeApi:
    """MaxApi без сети: сценарий ответов GET /updates, журнал вызовов."""

    instances: ClassVar[list[FakeApi]] = []
    next_script: ClassVar[list] = []  # ответы /updates для следующего экземпляра: задаются до старта бота

    def __init__(self, token="", base_url="", ca_bundle_file=None):
        self.args = (token, base_url, ca_bundle_file)
        self.calls: list[tuple] = []
        self.subs: list[dict] | Exception = []
        self.commands_error: Exception | None = None
        self.me: dict | Exception = {"name": "Корсик", "username": "korsik_test_bot"}
        self.script: list = list(FakeApi.next_script)
        self.idle = asyncio.Event()
        self.closed = False
        FakeApi.instances.append(self)

    async def get_subscriptions(self):
        self.calls.append(("get_subscriptions",))
        if isinstance(self.subs, Exception):
            raise self.subs
        return self.subs

    async def delete_subscription(self, url):
        self.calls.append(("delete_subscription", url))

    async def set_commands(self, commands):
        self.calls.append(("set_commands", commands))
        if self.commands_error:
            raise self.commands_error

    async def get_me(self):
        self.calls.append(("get_me",))
        if isinstance(self.me, Exception):
            raise self.me
        return self.me

    async def get_updates(self, marker=None, timeout=30, limit=100, types=None):
        self.calls.append(("get_updates", marker, timeout, tuple(types or ())))
        if not self.script:
            self.idle.set()
            await asyncio.Event().wait()  # long polling без событий — ждём, пока остановят
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.closed = True


async def test_startup_drops_webhooks_and_sets_menu(monkeypatch):
    monkeypatch.setattr(settings, "demo_enabled", False)
    api = FakeApi()
    api.subs = [{"url": "https://old-hook.test/a"}, {"time": 1}]
    await runner._startup(api)
    assert api.calls == [("get_subscriptions",), ("delete_subscription", "https://old-hook.test/a"),
                         ("set_commands", COMMANDS), ("get_me",)]


async def test_startup_failures_do_not_stop_bot(caplog):
    api = FakeApi()
    api.subs = MaxApiError(500, "", "boom")
    api.commands_error = httpx.ConnectError("нет сети")
    api.me = MaxApiError(401, "verify.token")
    with caplog.at_level(logging.WARNING, logger="korsik.bot"):
        await runner._startup(api)  # не бросает
    assert [c[0] for c in api.calls] == ["get_subscriptions", "set_commands", "get_me"]
    assert caplog.text.count("WARNING") == 3


# --- long polling -------------------------------------------------------------------------------


@pytest.fixture
def fake_api(monkeypatch):
    FakeApi.instances.clear()
    monkeypatch.setattr(FakeApi, "next_script", [])
    monkeypatch.setattr(runner, "MaxApi", FakeApi)
    monkeypatch.setattr(runner, "ERROR_PAUSE", 0)
    monkeypatch.setattr(settings, "max_bot_token", "TOKEN")
    monkeypatch.setattr(settings, "max_api_base_url", "https://max.test")
    monkeypatch.setattr(settings, "ca_bundle_file", "certs/test.pem")
    yield FakeApi
    runtime.set_ctx(None)


async def _start(fake_api, script) -> tuple[asyncio.Task, FakeApi]:
    """Запустить бота и дождаться, пока он разберёт весь сценарий /updates и уйдёт в ожидание."""
    fake_api.next_script = script
    task = asyncio.create_task(runner.run_bot())
    while not fake_api.instances:
        await asyncio.sleep(0)
    api = fake_api.instances[-1]
    await asyncio.wait_for(api.idle.wait(), 5)
    return task, api


async def test_run_bot_marker_retry_and_cancel(fake_api, test_db, monkeypatch):
    await SqlRepo(test_db).set_setting(runner.MARKER_KEY, 10)
    processed = []

    async def process(ctx, upd):
        processed.append(upd["n"])
        assert runtime.get_ctx() is ctx, "планировщик и API шлют через тот же контекст"
        if upd["n"] == 1:
            raise RuntimeError("сломанное событие")

    monkeypatch.setattr(runner, "process_update", process)
    task, api = await _start(fake_api, [
        httpx.ReadTimeout("сеть моргнула"),
        {"updates": [{"n": 1, "update_type": "message_created"}, {"n": 2}], "marker": 12},
        {"updates": [], "marker": 12},
    ])
    assert api.args == ("TOKEN", "https://max.test", "certs/test.pem")
    polls = [c for c in api.calls if c[0] == "get_updates"]
    assert [c[1] for c in polls] == [10, 10, 12, 12], "маркер из БД, после сбоя — тот же, потом новый"
    assert all(c[2] == runner.POLL_TIMEOUT and c[3] == tuple(UPDATE_TYPES) for c in polls)
    assert processed == [1, 2], "ошибка одного события не останавливает пачку"
    assert (await test_db.get(Setting, runner.MARKER_KEY)).value == 12
    assert runtime.get_ctx() is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert api.closed and runtime.get_ctx() is None


async def test_run_bot_without_marker_saves_first_one(fake_api, test_db, monkeypatch):
    async def process(ctx, upd):
        return None

    monkeypatch.setattr(runner, "process_update", process)
    task, api = await _start(fake_api, [{"updates": [{"n": 1}], "marker": 5}])
    assert [c[1] for c in api.calls if c[0] == "get_updates"] == [None, 5]
    assert (await test_db.get(Setting, runner.MARKER_KEY)).value == 5
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_bot_cancel_during_event_stops_without_saving_marker(fake_api, test_db, monkeypatch):
    started = asyncio.Event()

    async def process(ctx, upd):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "process_update", process)
    fake_api.next_script = [{"updates": [{"n": 1}], "marker": 99}]
    task = asyncio.create_task(runner.run_bot())
    await asyncio.wait_for(started.wait(), 5)
    api = fake_api.instances[-1]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert api.closed and runtime.get_ctx() is None
    assert await test_db.get(Setting, runner.MARKER_KEY) is None, "пачка не обработана — событие придёт снова"


async def test_run_bot_without_token_does_nothing(fake_api, monkeypatch, caplog):
    monkeypatch.setattr(settings, "max_bot_token", "")
    with caplog.at_level(logging.WARNING, logger="korsik.bot"):
        await runner.run_bot()
    assert fake_api.instances == [] and runtime.get_ctx() is None
    assert "MAX_BOT_TOKEN" in caplog.text


async def test_updates_pause_respects_retry_after():
    """429 на /updates: ждём столько, сколько просит «МАКС», но не больше минуты; прочие ошибки — обычная пауза."""
    from app.bot.api import MaxApiError
    from app.bot.runner import ERROR_PAUSE, _error_pause

    assert _error_pause(MaxApiError(429, "too.many.requests", retry_after=7)) == 7
    assert _error_pause(MaxApiError(429, "too.many.requests", retry_after=600)) == 60
    assert _error_pause(MaxApiError(429, "too.many.requests")) == ERROR_PAUSE
    assert _error_pause(MaxApiError(502, "bad.gateway")) == ERROR_PAUSE
    assert _error_pause(OSError("сеть")) == ERROR_PAUSE


async def test_startup_menu_has_demo_only_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_enabled", True)
    api = FakeApi()
    api.subs = []
    await runner._startup(api)
    cmds = next(c[1] for c in api.calls if c[0] == "set_commands")
    assert {"name": "demo", "description": "Демо: сменить роль"} in cmds
    assert cmds[:len(COMMANDS)] == COMMANDS
    assert all(c["name"] != "demo" for c in COMMANDS)
