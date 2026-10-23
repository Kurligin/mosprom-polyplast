"""Фикстуры чатов команды: отдельная тестовая БД в том же Postgres, фейковый API «МАКС» с группами.

Все люди и чаты синтетические.
"""

from __future__ import annotations

import itertools
import math
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.bot import hooks
from app.bot.handlers import BotCtx
from app.bot.sender import Sender
from app.config import settings
from app.db.base import Base
from app.db.models import Employee, Site, Staff, TeamChat

TEST_DB = "korsik_test_chats"
MSK = ZoneInfo("Europe/Moscow")
START = date(2026, 11, 2)                                   # понедельник: первый рабочий день
DAY1 = datetime.combine(START, time(12, 0), tzinfo=MSK)     # «сейчас» новичка в день выхода
DAY0 = datetime.combine(date(2026, 11, 1), time(12, 0), tzinfo=MSK)
PRIVACY = {"success": True, "failed_user_ids": [100],
           "failed_user_details": [{"error_code": "add.participant.privacy", "user_ids": [100]}]}


class FakeApi:
    """Bot API без сети: сообщения, ответы на кнопки, правки, группы (GET /chats, addMembers)."""

    def __init__(self):
        self.sent: list[dict] = []
        self.answers: list[dict] = []
        self.edits: list[dict] = []
        self.members: list[tuple[int, list[int]]] = []
        self.member_result: dict[int, dict | Exception] = {}
        self.chats: dict[int, dict] = {}
        self._mid = itertools.count(1)

    async def send_message(self, text, *, chat_id=None, user_id=None, attachments=None, fmt=None, notify=True):
        mid = f"mid{next(self._mid)}"
        self.sent.append({"mid": mid, "text": text, "chat_id": chat_id, "user_id": user_id,
                          "attachments": attachments or [], "fmt": fmt})
        return {"message": {"body": {"mid": mid, "text": text}, "recipient": {"chat_id": chat_id}}}

    async def answer_callback(self, callback_id, *, message=None, notification=None):
        self.answers.append({"callback_id": callback_id, "message": message, "notification": notification})
        return {"success": True}

    async def edit_message(self, mid, text, *, attachments=None, fmt=None):
        self.edits.append({"mid": mid, "text": text, "attachments": attachments})
        return {"success": True}

    async def upload(self, kind, filename, data, content_type):
        return {"photos": {"p1": {"token": f"img-{filename}"}}}

    async def send_action(self, chat_id, action="typing_on"):
        return {"success": True}

    async def get_chat(self, chat_id):
        return self.chats.get(chat_id) or {
            "chat_id": chat_id, "type": "chat", "status": "active", "title": f"Чат {chat_id} (синтетика)",
            "participants_count": 7, "link": f"https://max.ru/join/chat{abs(chat_id)}"}

    async def add_members(self, chat_id, user_ids):
        self.members.append((chat_id, list(user_ids)))
        res = self.member_result.get(chat_id, {"success": True})
        if isinstance(res, Exception):
            raise res
        return res

    def to(self, chat_id) -> list[dict]:
        return [m for m in self.sent if m["chat_id"] == chat_id]

    @staticmethod
    def buttons(msg: dict) -> list[dict]:
        return [b for a in msg["attachments"] if a["type"] == "inline_keyboard"
                for row in a["payload"]["buttons"] for b in row]


async def _no_sleep(_):
    return None


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def sender(api):
    return Sender(api, per_chat_rate=1000, global_rate=1000, sleep=_no_sleep)


@pytest.fixture
def ctx(sender):
    return BotCtx(sender=sender, bot_name="korsik_test_bot")


@pytest.fixture(autouse=True)
def chats_hooks():
    """Кнопки ch: зарегистрированы, как в боте; реестр после теста — как был."""
    from app.chats import hooks as chats_hooks

    saved = dict(hooks._callbacks)
    hooks._callbacks["ch"] = chats_hooks.chats_button
    yield
    hooks._callbacks.clear()
    hooks._callbacks.update(saved)


async def _ensure_db(url) -> None:
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB})
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    finally:
        await admin.dispose()


@pytest_asyncio.fixture
async def db():
    """Фабрика сессий чистой тестовой БД: площадки GTP, GrPP, Luzhniki и сотрудники HR / руководитель."""
    url = make_url(settings.database_url)
    try:
        await _ensure_db(url)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres недоступен: {e}")
    engine = create_async_engine(url.set(database=TEST_DB), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for code in ("GrPP", "Luzhniki", "GTP"):
            s.add(Site(code=code, name=f"{code} (синтетика)", tz="Europe/Moscow"))
        await s.flush()
        s.add_all([Staff(full_name="Петрова Ольга (HR, синтетика)", role="hr", max_user_id=201, chat_id=601),
                   Staff(full_name="Волков Сергей (руководитель, синтетика)", role="manager", max_user_id=203,
                         chat_id=603)])
        await s.commit()
    yield factory
    await engine.dispose()


def make_employee(**kw) -> Employee:
    """Новичок GTP-производства (синтетические данные, но не is_synthetic: бот с ним работает)."""
    base = {"full_name": "Тестов Тест (синтетика)", "first_name": "Тест", "site_code": "GTP",
            "work_type": "production", "start_date": START, "probation_end": date(2027, 2, 1),
            "max_user_id": 100, "chat_id": 500, "status": "active", "address_form": "ty",
            "dialog_data": {"onboarded": True}, "is_test": True, "time_shift_hours": 0}
    base.update(kw)
    return Employee(**base)


def make_chat(chat_id: int, **kw) -> TeamChat:
    base = {"chat_id": chat_id, "title": f"Чат {chat_id} (синтетика)", "link": f"https://max.ru/join/chat{abs(chat_id)}",
            "audience": "all", "sites": [], "work_type": None, "for_intro": False, "active": True,
            "info_at": datetime.now(UTC)}  # сведения свежие: задача не перечитывает GET /chats
    base.update(kw)
    return TeamChat(**base)


def shift_to(when: datetime) -> int:
    """Сдвиг машины времени, чтобы «сейчас» новичка было не раньше when."""
    return math.ceil((when - datetime.now(UTC)).total_seconds() / 3600)


def ev_added(chat_id: int, user_id: int = 201) -> dict:
    return {"update_type": "bot_added", "chat_id": chat_id, "user": {"user_id": user_id}, "is_channel": False}


def ev_press(user_id: int, chat_id: int, msg: dict, payload: str, *, chat_type: str = "chat") -> dict:
    """Нажатие кнопки payload в сообщении msg (из FakeApi.sent)."""
    return {"update_type": "message_callback",
            "callback": {"callback_id": f"cb-{msg['mid']}-{payload}", "payload": payload,
                         "user": {"user_id": user_id}},
            "message": {"recipient": {"chat_id": chat_id, "chat_type": chat_type},
                        "body": {"mid": msg["mid"], "text": msg["text"], "attachments": msg["attachments"]}}}
