"""Фикстуры сценария: отдельная тестовая БД в том же Postgres, фейковый API «МАКС»."""

from __future__ import annotations

import itertools
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.bot.api import MaxApiError
from app.bot.sender import Sender
from app.config import settings
from app.db.base import Base
from app.db.models import Employee, Site

TEST_DB = "korsik_test_scenario"


class FakeApi:
    """Фейк Bot API: запоминает отправки, ответы на кнопки и загрузки."""

    def __init__(self):
        self.sent: list[dict] = []
        self.answers: list[dict] = []
        self.uploads: list[tuple[str, str]] = []
        self.fail_queue: list[Exception] = []
        self._mid = itertools.count(1)

    async def send_message(self, text, *, chat_id=None, user_id=None, attachments=None, fmt=None, notify=True):
        if self.fail_queue:
            raise self.fail_queue.pop(0)
        self.sent.append({"text": text, "chat_id": chat_id, "attachments": attachments or []})
        return {"message": {"body": {"mid": f"mid{next(self._mid)}", "text": text},
                            "recipient": {"chat_id": chat_id}}}

    async def answer_callback(self, callback_id, *, message=None, notification=None):
        self.answers.append({"callback_id": callback_id, "message": message, "notification": notification})
        return {"success": True}

    async def upload(self, kind, filename, data, content_type):
        self.uploads.append((kind, filename))
        if kind == "image":
            return {"photos": {"p1": {"token": f"img-{filename}"}}}
        return {"token": f"file-{filename}"}

    async def send_action(self, chat_id, action="typing_on"):
        return {"success": True}

    def payloads(self, idx=-1) -> list[str]:
        return [b["payload"] for a in self.sent[idx]["attachments"] if a["type"] == "inline_keyboard"
                for row in a["payload"]["buttons"] for b in row]

    def media(self, idx=-1) -> list[str]:
        return [a["type"] for a in self.sent[idx]["attachments"] if a["type"] != "inline_keyboard"]


async def _no_sleep(_):
    return None


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def sender(api):
    return Sender(api, sleep=_no_sleep)


@pytest.fixture
def not_ready():
    return MaxApiError(400, "attachment.not.ready", "Key: errors.process.attachment.file.not.processed")


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
    """Фабрика сессий чистой тестовой БД (все таблицы пустые)."""
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
        await s.commit()
    yield factory
    await engine.dispose()


def make_employee(**kw) -> Employee:
    """Новичок (синтетика): выход в пн 2 ноября 2026, знакомство пройдено."""
    base = {"full_name": "Тестов Тест (синтетика)", "first_name": "Тест", "site_code": "GTP",
            "work_type": "production", "start_date": date(2026, 11, 2), "probation_end": date(2027, 2, 1),
            "max_user_id": 100, "chat_id": 500, "status": "active", "address_form": "ty",
            "dialog_data": {"onboarded": True}, "is_test": True, "time_shift_hours": 0}
    base.update(kw)
    return Employee(**base)
