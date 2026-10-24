"""Сквозной тест «путь сотрудника»: фейковый API «МАКС», отдельная тестовая БД, подмены окружения.

Прогон идёт один раз на модуль (фикстура `journey`), тесты проверяют его по этапам.
В сеть ничего не уходит: «МАКС» — FakeMax, эмбеддинги и LLM подменены, ИИ-разбор выключен.
"""

from __future__ import annotations

import asyncio
import importlib
import itertools
import logging
import sys
from collections import defaultdict

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.e2e import journey as J

TEST_DB = "korsik_test_e2e"


# --- фейковый «МАКС» --------------------------------------------------------------

class FakeMax:
    """Bot API «МАКС» без сети: сообщения по mid, правки на месте, ответы на кнопки, загрузки файлов.

    `log` — общая лента прогона: send / answer / edit / toast от бота и act — действия людей (пишет прогон).
    """

    def __init__(self):
        self.msgs: dict[str, dict] = {}
        self.log: list[dict] = []
        self.uploads: list[tuple[str, str]] = []
        self.other: list[tuple[str, tuple, dict]] = []
        self.chats: dict[int, dict] = {}          # групповые чаты: ответ GET /chats/{id}
        self.member_errors: dict[int, str] = {}   # чат → код отказа addMembers (add.participant.privacy)
        self._mid = itertools.count(1)

    def _entry(self, op: str, mid: str | None, chat_id, text: str, atts: list[dict]) -> None:
        self.log.append({"op": op, "mid": mid, "chat_id": chat_id, "text": text, "attachments": list(atts or [])})

    async def send_message(self, text, *, chat_id=None, user_id=None, attachments=None, fmt=None, notify=True):
        mid = f"m{next(self._mid)}"
        atts = list(attachments or [])
        self.msgs[mid] = {"chat_id": chat_id, "user_id": user_id, "text": text, "attachments": atts}
        self._entry("send", mid, chat_id, text, atts)
        return {"message": {"body": {"mid": mid, "text": text}, "recipient": {"chat_id": chat_id}}}

    async def answer_callback(self, callback_id, *, message=None, notification=None):
        mid = (callback_id or "").split("|", 1)[0]
        msg = self.msgs.setdefault(mid, {"chat_id": None, "text": "", "attachments": []})
        if message is not None:
            msg["text"] = message.get("text") or ""
            msg["attachments"] = list(message.get("attachments") or [])
            self._entry("answer", mid, msg.get("chat_id"), msg["text"], msg["attachments"])
        if notification:
            self._entry("toast", mid, msg.get("chat_id"), notification, [])
        return {"success": True}

    async def edit_message(self, mid, text, *, attachments=None, fmt=None):
        msg = self.msgs.setdefault(mid, {"chat_id": None, "text": "", "attachments": []})
        msg["text"] = text
        if attachments is not None:
            msg["attachments"] = list(attachments)
        self._entry("edit", mid, msg.get("chat_id"), text, msg["attachments"])
        return {"success": True}

    async def delete_message(self, mid):
        msg = self.msgs.pop(mid, {})
        self._entry("delete", mid, msg.get("chat_id"), "", [])
        return {"success": True}

    async def upload(self, kind, filename, data, content_type):
        self.uploads.append((kind, filename))
        if kind == "image":
            return {"photos": {"p1": {"token": f"img-{filename}"}}}
        return {"token": f"file-{filename}"}

    async def send_action(self, chat_id, action="typing_on"):
        return {"success": True}

    async def get_chat(self, chat_id):
        info = self.chats.get(chat_id) or {}
        return {"chat_id": chat_id, "type": "chat", "status": "active", "participants_count": 2, **info}

    async def add_members(self, chat_id, user_ids):
        """addMembers: успех или отказ из member_errors. В ленте прогона — op "members"."""
        code = self.member_errors.get(chat_id)
        self.log.append({"op": "members", "mid": None, "chat_id": chat_id, "text": code or "",
                         "attachments": [], "user_ids": list(user_ids)})
        if code:
            return {"success": True, "failed_user_ids": list(user_ids),
                    "failed_user_details": [{"error_code": code, "user_ids": list(user_ids)}]}
        return {"success": True}

    def __getattr__(self, name):
        # новые методы API (например, добавление в чаты) — запоминаем вызов и отвечаем «успех»
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*args, **kwargs):
            self.other.append((name, args, kwargs))
            return {"success": True}

        return call


# --- тестовая БД ----------------------------------------------------------------------

async def _ensure_db(url) -> None:
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB})
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    finally:
        await admin.dispose()


async def _clean_db():
    """Чистая БД korsik_test_e2e: все таблицы пустые, счётчики id с единицы."""
    from app.config import settings
    from app.db.base import Base

    url = make_url(settings.database_url)
    await _ensure_db(url)
    engine = create_async_engine(url.set(database=TEST_DB), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# --- подключение модулей и подмены ---------------------------------------------------------

def _hooks_snapshot():
    from app.bot import hooks

    return (dict(hooks._callbacks), dict(hooks._states), dict(hooks._commands), list(hooks._pre_routes),
            dict(hooks._single))


def _hooks_restore(saved) -> None:
    from app.bot import hooks

    for target, value in zip((hooks._callbacks, hooks._states, hooks._commands, hooks._pre_routes, hooks._single),
                             saved, strict=True):
        target.clear()
        if isinstance(target, list):
            target.extend(value)
        else:
            target.update(value)


def _load_plugins() -> list[str]:
    """Все модули бота строго в порядке app/plugins.py, как в проде.

    Порядок важен: pre_route выполняются по очереди регистрации. Другие тесты могли импортировать
    hooks-модули раньше (например, tests/signals — app.signals.hooks при сборке), поэтому реестр
    собираем заново: reset и повторный импорт. После прогона реестр восстанавливается (_hooks_restore).
    """
    from app import plugins
    from app.bot import hooks

    hooks.reset()
    for name in plugins.MODULES:
        try:
            if name in sys.modules:
                importlib.reload(sys.modules[name])
            else:
                importlib.import_module(name)
        except Exception:  # как load_plugins: модуль не загрузился, остальные работают
            logging.getLogger(__name__).exception("Модуль %s не загружен", name)
    return plugins.load_plugins()


def _patch(mp: pytest.MonkeyPatch, factory, run: J.Journey) -> None:
    import app.db.session as db_session
    from app.config import settings
    from app.kb import embed as kb_embed
    from app.kb import search as kb_search
    from app.kb import service as kb_service
    from app.llm import gigachat
    from app.scenario import engine
    from app.signals import service as signals

    # путь настоящего сотрудника по кодам приглашения, как на проде (DEMO_ENABLED=false):
    # без подсказки демо-режима при первом входе и без меню ролей
    mp.setattr(settings, "demo_enabled", False)
    # вся работа с БД вне обработчиков (тик, задачи, кэш вложений) — в тестовую базу
    mp.setattr(db_session, "async_session", factory)
    # ИИ-разбор ответов опросов уходит в LLM в фоне — в e2e выключен
    mp.setattr(signals, "AI_ENABLED", False)

    # база знаний без эмбеддингов: поиск — фейк с пунктом ПВТР про обед
    mp.setattr(kb_service, "search", run.fake_search)
    mp.setattr(kb_search, "search", run.fake_search)

    def no_embeddings(*a, **kw):
        raise AssertionError("e2e: модель эмбеддингов не поднимаем")

    for name in ("embed", "embed_one", "embed_sync", "get_model"):
        mp.setattr(kb_embed, name, no_embeddings)

    async def no_llm_choose(*a, **kw):
        run.llm_calls.append("choose_chunk")
        raise gigachat.LlmUnavailable("e2e: LLM выключен")

    async def no_llm_analyze(*a, **kw):
        run.llm_calls.append("analyze_answer")

    mp.setattr(gigachat, "choose_chunk", no_llm_choose)
    mp.setattr(gigachat, "analyze_answer", no_llm_analyze)

    # «отправить шаги сразу после перемотки» — та же функция движка, но после коммита и без паузы
    mp.setattr(engine, "_deliver_soon", run.queue_deliver_soon)
    mp.setattr(engine, "_locks", defaultdict(asyncio.Lock))


async def _run(mp: pytest.MonkeyPatch) -> J.Journey:
    from app.bot import runtime
    from app.surveys import config as survey_config
    from app.surveys import store as survey_store

    try:
        engine, factory = await _clean_db()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres недоступен: {e}")

    api = FakeMax()
    run = J.Journey(factory, api)
    saved_hooks = _hooks_snapshot()
    handler = J.LogCapture(run)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        run.plugins = _load_plugins()
        _patch(mp, factory, run)
        async with factory() as s:
            # опросы из config/surveys.yaml; кэш id вопросов — для этой базы
            survey_config.load()
            ids = await survey_store.sync(s)
            await s.commit()
        mp.setitem(survey_store._ids, "map", ids)
        mp.setitem(survey_store._ids, "mtime", survey_config._cache["mtime"])
        runtime.set_ctx(run.ctx)
        await run.run()
    finally:
        runtime.set_ctx(None)
        root.removeHandler(handler)
        _hooks_restore(saved_hooks)
        await engine.dispose()
    run.write_md()
    return run


@pytest.fixture(scope="module")
def journey() -> J.Journey:
    """Весь путь новичка один раз на модуль; маршрут пишется в eval/journey/gtp-operator.md."""
    if not J.XLSX.exists():
        pytest.skip(f"Нет {J.XLSX.relative_to(J.ROOT)}: сценарий не из чего импортировать")
    with pytest.MonkeyPatch.context() as mp:
        return asyncio.run(_run(mp))
