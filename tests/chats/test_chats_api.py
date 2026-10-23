"""API чатов команды для Mini App: список, правила «кому», HR-группа, чаты в карточке новичка.

Тестовая БД из conftest.py, вход по подписанному initData, бот — фейковый Sender. Данные синтетические.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.auth import sign
from app.api.deps import get_session
from app.api.router import router
from app.bot import runtime
from app.bot.repo import SqlRepo
from app.chats import service
from app.chats import texts as T
from app.config import settings
from app.db.models import Employee, Setting, TeamChat
from tests.chats.conftest import DAY1, PRIVACY, make_chat, make_employee

pytestmark = pytest.mark.asyncio
TOKEN = "123456:TEST-TOKEN"
HR, MANAGER = 201, 203


def auth(user_id: int) -> dict:
    data = {"auth_date": str(int(time.time())), "query_id": "AAH-test",
            "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False)}
    raw = "&".join(f"{k}={quote(v, safe='')}" for k, v in data.items())
    return {"X-Max-Init-Data": f"{raw}&hash={sign(data, TOKEN)}"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "max_bot_token", TOKEN)
    monkeypatch.setattr(settings, "dev_allow_no_init_data", False)


@pytest_asyncio.fixture
async def client(db):
    app = FastAPI()
    app.include_router(router)

    async def _session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def bot(sender):
    runtime.set_ctx(SimpleNamespace(sender=sender))
    yield sender
    runtime.set_ctx(None)


async def seed(db) -> None:
    async with db() as s:
        s.add_all([make_chat(-1, title="Общий чат", members_count=120),
                   make_chat(-2, title="Команда GTP", audience="sites", sites=["GTP"], work_type="production",
                             for_intro=True, prompt_mid="mid-old"),
                   make_chat(-7, title="Старый чат", active=False),
                   make_chat(-9, title="HR", audience="none")])
        await SqlRepo(s).set_setting(service.HR_KEY, -9)
        await s.commit()


async def test_list_chats(db, client):
    await seed(db)
    r = await client.get("/api/chats", headers=auth(HR))
    assert r.status_code == 200
    rows = r.json()
    assert [c["id"] for c in rows] == [-9, -2, -1, -7], "HR-группа первой, без бота — в конце"
    hr, team, general, old = rows
    assert hr["is_hr_group"] and hr["audience"] == "none"
    assert team == {**team, "title": "Команда GTP", "audience": "sites", "sites": ["GTP"], "work_type": "production",
                    "for_intro": True, "is_hr_group": False, "active": True, "added": 0, "links_sent": 0}
    assert general["members_count"] == 120 and general["link"] == "https://max.ru/join/chat1"
    assert not old["active"]


async def test_list_counts_newcomers(db, client, sender, api):
    await seed(db)
    api.member_result = {-1: PRIVACY}
    async with db() as s:
        s.add(make_employee())
        await s.commit()
    await service.tick(DAY1, sender=sender, session_factory=db)
    rows = {c["id"]: c for c in (await client.get("/api/chats", headers=auth(HR))).json()}
    assert (rows[-1]["added"], rows[-1]["links_sent"]) == (0, 1)
    assert (rows[-2]["added"], rows[-2]["links_sent"]) == (1, 0)


async def test_legacy_hr_group_shows_up(db, client):
    async with db() as s:
        await SqlRepo(s).set_setting(service.HR_KEY, -555)  # выбрана до учёта чатов: только Setting
        await s.commit()
    rows = (await client.get("/api/chats", headers=auth(HR))).json()
    assert [(c["id"], c["title"], c["is_hr_group"]) for c in rows] == [(-555, "HR-группа", True)]


async def test_patch_rules(db, client):
    await seed(db)
    r = await client.patch("/api/chats/-1", headers=auth(HR),
                           json={"audience": "sites", "sites": ["GTP", "GrPP", "GTP"], "work_type": "office"})
    assert r.status_code == 200, r.text
    assert (r.json()["audience"], r.json()["sites"], r.json()["work_type"]) == ("sites", ["GTP", "GrPP"], "office")
    r = await client.patch("/api/chats/-1", headers=auth(HR), json={"work_type": "all", "for_intro": True})
    assert r.json()["work_type"] is None and r.json()["for_intro"] and r.json()["sites"] == ["GTP", "GrPP"]
    r = await client.patch("/api/chats/-1", headers=auth(HR), json={"audience": "all"})
    assert r.json()["audience"] == "all" and r.json()["for_intro"], "непереданные поля не меняются"
    async with db() as s:
        chat = await s.get(TeamChat, -1)
        assert chat.audience == "all" and chat.work_type is None


@pytest.mark.parametrize("body, code", [
    ({"sites": ["Марс"]}, 400),
    ({"audience": "everyone"}, 422),
    ({"work_type": "remote"}, 422),
])
async def test_patch_validation(db, client, body, code):
    await seed(db)
    r = await client.patch("/api/chats/-1", headers=auth(HR), json=body)
    assert r.status_code == code
    if code == 400:
        assert "Марс" in r.json()["detail"]


async def test_hr_group_rules_rejected_and_unknown_chat(db, client):
    await seed(db)
    r = await client.patch("/api/chats/-9", headers=auth(HR), json={"audience": "all"})
    assert r.status_code == 400 and "HR-группу" in r.json()["detail"]
    r = await client.patch("/api/chats/-2", headers=auth(HR), json={"is_hr_group": True, "for_intro": True})
    assert r.status_code == 400
    assert (await client.patch("/api/chats/-404", headers=auth(HR), json={"for_intro": True})).status_code == 404
    r = await client.patch("/api/chats/-7", headers=auth(HR), json={"is_hr_group": True})
    assert r.status_code == 409


async def test_reassign_hr_group_from_mini_app(db, client, bot, api):
    await seed(db)
    r = await client.patch("/api/chats/-2", headers=auth(HR), json={"is_hr_group": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["is_hr_group"] and data["audience"] == "none" and not data["for_intro"] and data["sites"] == []
    async with db() as s:
        assert (await s.get(Setting, service.HR_KEY)).value == -2
        assert (await s.get(TeamChat, -2)).prompt_mid is None
    # кнопка «Сделать HR-группой» в этом чате превратилась в «Готово» на месте, новых сообщений нет
    assert api.edits == [{"mid": "mid-old", "text": T.HR_GROUP_DONE, "attachments": []}] and not api.sent
    rows = {c["id"]: c for c in (await client.get("/api/chats", headers=auth(HR))).json()}
    assert not rows[-9]["is_hr_group"] and rows[-2]["is_hr_group"]
    # чат без сообщения с кнопкой: бот коротко пишет в новой HR-группе
    await client.patch("/api/chats/-1", headers=auth(HR), json={"audience": "none", "is_hr_group": True})
    assert [(m["chat_id"], m["text"]) for m in api.sent] == [(-1, T.HR_GROUP_DONE)]
    # снять отметку: HR-группы нет
    r = await client.patch("/api/chats/-1", headers=auth(HR), json={"is_hr_group": False})
    async with db() as s:
        assert (await s.get(Setting, service.HR_KEY)).value is None


async def test_manager_forbidden(db, client):
    await seed(db)
    assert (await client.get("/api/chats", headers=auth(MANAGER))).status_code == 403
    assert (await client.patch("/api/chats/-1", headers=auth(MANAGER), json={"audience": "none"})).status_code == 403
    assert (await client.get("/api/chats")).status_code == 401


async def test_card_shows_chats(db, client, sender, api):
    await seed(db)
    api.member_result = {-1: PRIVACY}
    async with db() as s:
        s.add(make_chat(-3, title="Лужники", audience="sites", sites=["Luzhniki"]))
        emp = make_employee()
        s.add(emp)
        await s.commit()
        emp_id = emp.id
    r = await client.get(f"/api/employees/{emp_id}", headers=auth(HR))
    assert r.status_code == 200, r.text
    assert sorted((c["chat_id"], c["status"]) for c in r.json()["chats"]) == [(-2, "pending"), (-1, "pending")]
    await service.tick(DAY1, sender=sender, session_factory=db)
    async with db() as s:
        e = await s.get(Employee, emp_id)
        service._set_data(e, intro_post={"status": "published", "chat_id": -2, "at": "2026-11-03T10:00:00+00:00"})
        await s.commit()
    chats = {c["chat_id"]: c for c in (await client.get(f"/api/employees/{emp_id}", headers=auth(HR))).json()["chats"]}
    assert chats[-1]["status"] == "link" and chats[-1]["reason"] == "не пустили настройки приватности"
    assert chats[-1]["title"] == "Общий чат" and chats[-1]["link"]
    assert chats[-2]["status"] == "added" and chats[-2]["intro"] and chats[-2]["reason"] is None
    assert set(chats) == {-1, -2}


async def test_card_synthetic_has_no_chats(db, client):
    await seed(db)
    async with db() as s:
        emp = make_employee(is_synthetic=True, is_test=False)
        s.add(emp)
        await s.commit()
    r = await client.get(f"/api/employees/{emp.id}", headers=auth(HR))
    assert r.json()["chats"] == []
