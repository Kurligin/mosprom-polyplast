"""Фейковый «МАКС» для тестов опросов и сигналов: помнит каждое сообщение и его правки.

Сессия БД — настоящая (Postgres из compose) внутри транзакции, которая откатывается после теста.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.bot import hooks, runtime
from app.bot.handlers import BotCtx, handle_update
from app.bot.repo import SqlRepo
from app.bot.sender import Sender
from app.config import settings
from app.db.models import Employee, Site, Staff


class FakeApi:
    """Сообщения по mid: текст и клавиатура. Ответ на кнопку меняет то же сообщение."""

    def __init__(self):
        self.msgs: dict[str, dict] = {}
        self.log: list[tuple[str, str]] = []   # (действие, mid) — send / edit / answer
        self.toasts: list[str] = []
        self._mid = itertools.count(1)

    async def send_message(self, text, *, chat_id=None, user_id=None, attachments=None, fmt=None, notify=True):
        mid = f"m{next(self._mid)}"
        self.msgs[mid] = {"text": text, "attachments": attachments or [], "chat_id": chat_id}
        self.log.append(("send", mid))
        return {"message": {"body": {"mid": mid, "text": text}, "recipient": {"chat_id": chat_id}}}

    async def answer_callback(self, callback_id, *, message=None, notification=None):
        mid = callback_id.split("|", 1)[0]
        if message is not None:
            self.msgs[mid] = {**self.msgs.get(mid, {}), "text": message["text"],
                              "attachments": message.get("attachments") or []}
            self.log.append(("answer", mid))
        if notification:
            self.toasts.append(notification)
        return {"success": True}

    async def edit_message(self, mid, text, *, attachments=None, fmt=None):
        msg = self.msgs.setdefault(mid, {})
        msg["text"] = text
        if attachments is not None:
            msg["attachments"] = attachments
        self.log.append(("edit", mid))
        return {"success": True}

    async def delete_message(self, mid):
        self.msgs.pop(mid, None)
        return {"success": True}

    async def send_action(self, chat_id, action="typing_on"):
        return {"success": True}

    # --- удобства для тестов ---

    def sent(self, chat_id=None) -> list[str]:
        return [mid for act, mid in self.log if act == "send"
                and (chat_id is None or self.msgs.get(mid, {}).get("chat_id") == chat_id)]

    def last(self, chat_id=None) -> str:
        return self.sent(chat_id)[-1]

    def text(self, mid=None) -> str:
        return self.msgs[mid or self.last()]["text"]

    def buttons(self, mid=None) -> list[tuple[str, str]]:
        atts = self.msgs[mid or self.last()]["attachments"] or []
        return [(b["text"], b.get("payload")) for a in atts if a.get("type") == "inline_keyboard"
                for row in a["payload"]["buttons"] for b in row]

    def rows(self, mid=None) -> list[list[str]]:
        atts = self.msgs[mid or self.last()]["attachments"] or []
        return [[b["text"] for b in row] for a in atts if a.get("type") == "inline_keyboard"
                for row in a["payload"]["buttons"]]


_cb = itertools.count(1)


def ev_press(api: FakeApi, mid: str, label: str, user_id: int = 100, chat_id: int = 500) -> dict:
    """Нажатие кнопки с подписью label (подпись можно без «✓ ») в сообщении mid."""
    btns = api.buttons(mid)
    payload = next((p for t, p in btns if t == label or t.removeprefix("✓ ") == label), None)
    assert payload, f"Нет кнопки «{label}» в {mid}: {btns}"
    msg = api.msgs[mid]
    return {"update_type": "message_callback",
            "callback": {"callback_id": f"{mid}|{next(_cb)}", "payload": payload, "user": {"user_id": user_id}},
            "message": {"recipient": {"chat_id": chat_id, "chat_type": "dialog"},
                        "body": {"mid": mid, "text": msg["text"], "attachments": msg["attachments"]}}}


def ev_text(text: str, user_id: int = 100, chat_id: int = 500) -> dict:
    return {"update_type": "message_created",
            "message": {"sender": {"user_id": user_id, "name": "U"},
                        "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
                        "body": {"mid": f"in{next(_cb)}", "text": text}}}


async def _no_sleep(_):
    return None


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield s
        finally:
            await s.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def world(session, monkeypatch):
    """Площадка, рекрутер, руководитель, подключённый новичок; бот на фейковом API; опросы в БД."""
    from app.signals import service as signals
    from app.surveys import config as C
    from app.surveys import hooks as survey_hooks
    from app.surveys import store

    monkeypatch.setattr(signals, "AI_ENABLED", False)
    # Регистрации опросов — явно (другие тесты могли сбросить реестр), фабрика обращений — встроенная
    monkeypatch.setitem(hooks._callbacks, "sv", survey_hooks.survey_button)
    monkeypatch.setitem(hooks._states, "sv", survey_hooks.survey_text)
    monkeypatch.setattr(hooks, "_single", {})
    monkeypatch.setattr(hooks, "_pre_routes", [])

    C.load()
    monkeypatch.setitem(store._ids, "map", await store.sync(session))
    monkeypatch.setitem(store._ids, "mtime", C._cache["mtime"])

    api = FakeApi()
    ctx = BotCtx(sender=Sender(api, sleep=_no_sleep), bot_name="korsik_test_bot")
    runtime.set_ctx(ctx)
    repo = SqlRepo(session)
    if await session.get(Site, "GrPP") is None:
        session.add(Site(code="GrPP", name="GrPP", tz="Europe/Moscow"))
    if await session.get(Site, "GTP") is None:
        session.add(Site(code="GTP", name="GTP", tz="Europe/Moscow"))
    rec = Staff(full_name="Рекрутова Ирина (тест)", role="recruiter")
    mgr = Staff(full_name="Начальников Пётр (тест)", role="manager")
    session.add_all([rec, mgr])
    await session.flush()
    await repo.set_setting("hr_group_chat_id", 900)
    emp = Employee(full_name="Опросова Анна [тест]", first_name="Анна", site_code="GrPP", work_type="office",
                   start_date=date.today() - timedelta(days=2), recruiter_id=rec.id, manager_id=mgr.id,
                   address_form="ty", max_user_id=100, chat_id=500, status="active", dialog_data={"onboarded": True})
    session.add(emp)
    await session.flush()

    class World:
        pass

    w = World()
    w.session, w.repo, w.api, w.ctx, w.emp, w.rec, w.mgr = session, repo, api, ctx, emp, rec, mgr

    async def press(label: str, mid: str | None = None):
        await handle_update(ctx, repo, ev_press(api, mid or w.mid(), label))
        await session.flush()

    async def say(text: str):
        await handle_update(ctx, repo, ev_text(text))
        await session.flush()

    def mid() -> str:
        return (emp.dialog_data or {}).get("survey", {}).get("mid") or api.last(500)

    w.press, w.say, w.mid = press, say, mid
    try:
        yield w
    finally:
        runtime.set_ctx(None)
