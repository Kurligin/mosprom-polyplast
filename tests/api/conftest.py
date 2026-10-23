"""Фикстуры API: реальная БД в транзакции с откатом, подписанный initData, фейковый бот.

Все люди — синтетические.
"""

from __future__ import annotations

import json
import random
import time
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.auth import sign
from app.api.deps import get_session
from app.api.router import router
from app.bot import runtime
from app.config import settings
from app.db.models import Employee, Site, Staff

TOKEN = "123456:TEST-TOKEN"


def init_data(user_id: int, *, token: str = TOKEN, auth_date: int | None = None, drop_hash: bool = False) -> str:
    """initData как в «МАКС»: значения URL-кодированы, hash — HMAC от декодированных значений."""
    data = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Тест", "last_name": "Синтетика"}, ensure_ascii=False),
    }
    raw = "&".join(f"{k}={quote(v, safe='')}" for k, v in data.items())
    return raw if drop_hash else f"{raw}&hash={sign(data, token)}"


def auth(person) -> dict:
    return {"X-Max-Init-Data": init_data(person.max_user_id)}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "max_bot_token", TOKEN)
    monkeypatch.setattr(settings, "max_bot_name", "korsik_test_bot")
    monkeypatch.setattr(settings, "dev_allow_no_init_data", False)


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
async def client(session):
    app = FastAPI()
    app.include_router(router)

    async def _session():
        yield session

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class FakeSender:
    """Очередь отправки «МАКС» без сети: запоминает, что ушло."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.reject_open_app = False
        self._n = 0

    async def send(self, repo, text, *, chat_id=None, user_id=None, attachments=None, employee=None, staff=None,
                   ticket_id=None, fmt="markdown"):
        if self.reject_open_app and any(b.get("type") == "open_app" for a in attachments or []
                                        for row in a["payload"]["buttons"] for b in row):
            raise RuntimeError("400 open_app not allowed")
        self._n += 1
        mid = f"mid-{self._n}"
        self.sent.append({"text": text, "chat_id": chat_id, "user_id": user_id, "attachments": attachments,
                          "ticket_id": ticket_id})
        if repo is not None:
            await repo.add_message(employee_id=getattr(employee, "id", None), staff_id=getattr(staff, "id", None),
                                   chat_id=chat_id, direction="out", text=text, mid=mid, ticket_id=ticket_id)
        return [mid]

    async def edit(self, mid, text, *, attachments=None, key=None):
        self.edits.append({"mid": mid, "text": text, "attachments": attachments})
        return True

    async def answer(self, callback_id, *, message=None, notification=None, key=None):
        return {"success": True}


def buttons(atts) -> list[str]:
    return [b.get("payload") or b["type"] for a in atts or [] for row in a["payload"]["buttons"] for b in row]


@pytest.fixture
def bot():
    sender = FakeSender()
    runtime.set_ctx(SimpleNamespace(sender=sender))
    yield sender
    runtime.set_ctx(None)


@pytest_asyncio.fixture
async def world(session):
    """Площадка, персонал и два новичка внутри транзакции теста."""
    base = random.randint(10**12, 9 * 10**12)
    site = Site(code=f"T{base % 10**8}", name="Тестовая площадка (синтетика)", tz="Europe/Moscow")
    session.add(site)
    await session.flush()
    hr = Staff(full_name="Тестова Анна Сергеевна (HR, синтетика)", role="hr", max_user_id=base + 1)
    rec = Staff(full_name="Подборова Мария Игоревна (рекрутер, синтетика)", role="recruiter",
                max_user_id=base + 2, chat_id=base + 20)
    mgr = Staff(full_name="Начальников Пётр Андреевич (руководитель, синтетика)", role="manager",
                site_code=site.code, max_user_id=base + 3)
    mgr2 = Staff(full_name="Другой Руководитель (синтетика)", role="manager", max_user_id=base + 4)
    head = Staff(full_name="Главная Ольга Викторовна (глава подбора, синтетика)", role="head_recruitment",
                 max_user_id=base + 5)
    session.add_all([hr, rec, mgr, mgr2, head])
    await session.flush()
    start = date.today() - timedelta(days=4)
    emp = Employee(full_name="Синтетиков Демьян [синтетика]", first_name="Демьян", site_code=site.code,
                   work_type="production", start_date=start, probation_end=start + timedelta(days=90),
                   manager_id=mgr.id, recruiter_id=rec.id, max_user_id=base + 6, chat_id=base + 60,
                   status="active", address_form="ty", is_synthetic=True,
                   dialog_data={"position": "Оператор экструдера"})
    other = Employee(full_name="Примеров Макет [синтетика]", first_name="Макет", site_code=site.code,
                     work_type="office", start_date=start, probation_end=start + timedelta(days=90),
                     manager_id=mgr2.id, recruiter_id=rec.id, is_synthetic=True)
    session.add_all([emp, other])
    await session.flush()
    return SimpleNamespace(site=site, hr=hr, rec=rec, mgr=mgr, mgr2=mgr2, head=head, emp=emp, other=other,
                           group_chat=base + 999)
