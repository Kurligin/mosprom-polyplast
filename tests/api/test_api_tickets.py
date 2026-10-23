"""Обращения через API: очередь, лента, ответ новичку, «Взять», «Закрыть»."""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.bot.repo import SqlRepo
from app.db.models import Flag, HrAnswer, Message, Ticket
from app.tickets import service
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio


async def _ticket(session, world, text="Когда приходит аванс? (синтетика)", **kw) -> Ticket:
    repo = SqlRepo(session)
    await repo.add_message(employee_id=world.emp.id, staff_id=None, chat_id=world.emp.chat_id, direction="in",
                           text=text, mid="in-1")
    return await service.create_ticket(None, repo, world.emp, text, source=kw.pop("source", "hr_command"), **kw)


async def test_queue_red_first_and_status_filter(client, world, session):
    plain = await _ticket(session, world)
    red = await _ticket(session, world, "На линии не работает аварийная кнопка", source="flag", category="safety")
    closed = await _ticket(session, world, "Старый вопрос")
    closed.status = "closed"
    await session.flush()

    rows = (await client.get("/api/tickets?status=open", headers=auth(world.hr))).json()
    ids = [r["id"] for r in rows]
    assert closed.id not in ids
    assert ids.index(red.id) < ids.index(plain.id)
    r = next(x for x in rows if x["id"] == red.id)
    assert r["red_flag"] is True and r["category"] == "safety" and r["category_label"] == "Охрана труда"
    assert r["status"] == "open" and r["due_at"] is not None and r["day"] == service.employee_day(world.emp)
    p = next(x for x in rows if x["id"] == plain.id)
    assert p["category"] == "question" and p["red_flag"] is False
    assert p["preview"] == "Когда приходит аванс? (синтетика)"
    closed_rows = (await client.get("/api/tickets?status=closed", headers=auth(world.hr))).json()
    assert closed.id in [x["id"] for x in closed_rows]


async def test_ticket_feed(client, world, session):
    repo = SqlRepo(session)
    await repo.add_message(employee_id=world.emp.id, staff_id=None, chat_id=1, direction="out",
                           text="Вот что сказано:\n«Зарплата дважды в месяц»\nИсточник: ПВТР, п. 7.3, стр. 21")
    t = await _ticket(session, world)
    data = (await client.get(f"/api/tickets/{t.id}", headers=auth(world.hr))).json()
    authors = [m["author"] for m in data["messages"]]
    assert authors == ["bot", "employee"]
    assert data["messages"][0]["source"] == "ПВТР, п. 7.3, стр. 21"
    assert "Источник" not in data["messages"][0]["text"]
    assert data["messages"][1]["text"] == "Когда приходит аванс? (синтетика)"
    assert data["assignee"] == "Подборова М."  # закреплённый рекрутер


async def test_flag_ticket_shows_quote(client, world, session):
    t = await service.create_ticket(None, SqlRepo(session), world.emp, "Мастер кричит на смене",
                                    source="flag", category="травля")
    session.add(Flag(employee_id=world.emp.id, level="red", topic="harassment", quote=t.text, source="survey",
                     ticket_id=t.id))
    await session.flush()
    data = (await client.get(f"/api/tickets/{t.id}", headers=auth(world.hr))).json()
    assert data["category"] == "harassment" and data["red_flag"] is True
    assert [m["text"] for m in data["messages"]] == ["Мастер кричит на смене"]


async def test_reply_sends_and_moves_to_work(client, world, session, bot):
    t = await _ticket(session, world)
    r = await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr),
                          json={"text": "Аванс 25-го числа.", "add_to_kb": True, "kb_site": "all"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "in_progress" and data["assignee"] == "Тестова А."
    assert data["delivered"] is True and data["added_to_kb"] is True
    hr_msg = data["messages"][-1]
    assert hr_msg["author"] == "hr" and hr_msg["text"] == "Аванс 25-го числа."
    assert hr_msg["author_name"] == "Тестова Анна Сергеевна"
    # новичку — с подписью и подсказкой, что можно ответить
    sent = bot.sent[-1]
    assert sent["user_id"] == world.emp.max_user_id
    assert sent["text"].startswith("**Анна (HR)**\nАванс 25-го числа.")
    assert "напиши сюда" in sent["text"]
    ha = (await session.scalars(select(HrAnswer).where(HrAnswer.ticket_id == t.id))).one()
    assert ha.status == "draft" and ha.site_code is None and ha.question == t.text
    assert service.open_window(world.emp)["ticket_id"] == t.id

    # второй ответ — без подсказки
    await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr), json={"text": "Ещё деталь."})
    assert "напиши сюда" not in bot.sent[-1]["text"]


async def test_reply_not_delivered_when_not_connected(client, world, session, bot):
    repo = SqlRepo(session)
    t = await service.create_ticket(None, repo, world.other, "Вопрос без бота", source="hr_command")
    r = await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr),
                          json={"text": "Ответ (синтетика)", "add_to_kb": False, "kb_site": None})
    assert r.status_code == 200
    assert r.json()["delivered"] is False and r.json()["messages"][-1]["author"] == "hr"


async def test_personal_ticket_not_added_to_kb(client, world, session, bot):
    t = await _ticket(session, world, "Мне не пришла зарплата", source="personal", is_personal=True)
    r = await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr),
                          json={"text": "Проверим (синтетика)", "add_to_kb": True, "kb_site": "all"})
    assert r.json()["added_to_kb"] is False and r.json()["category_label"] == "Личный вопрос"
    assert (await session.scalars(select(HrAnswer).where(HrAnswer.ticket_id == t.id))).first() is None


async def test_take_close_edit_group(client, world, session, bot):
    await SqlRepo(session).set_setting("hr_group_chat_id", world.group_chat)
    t = await _ticket(session, world)
    assert t.group_mid
    r = await client.post(f"/api/tickets/{t.id}/take", headers=auth(world.rec), json={})
    assert r.json()["status"] == "in_progress" and r.json()["assignee"] == "Подборова М."
    assert bot.edits[-1]["mid"] == t.group_mid and "В работе: Подборова М." in bot.edits[-1]["text"]
    assert "tk:take:" not in str(bot.edits[-1]["attachments"])

    session.add(Flag(employee_id=world.emp.id, level="red", topic="safety", source="survey", ticket_id=t.id))
    await session.flush()
    r = await client.post(f"/api/tickets/{t.id}/close", headers=auth(world.hr), json={})
    assert r.json()["status"] == "closed" and r.json()["due_at"] is None
    assert "Закрыто" in bot.edits[-1]["text"]
    flag = (await session.scalars(select(Flag).where(Flag.ticket_id == t.id))).one()
    assert flag.status == "resolved"
    r = await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr), json={"text": "поздно"})
    assert r.status_code == 409
    assert (await client.post(f"/api/tickets/{t.id}/take", headers=auth(world.hr), json={})).status_code == 409


async def test_reply_validation(client, world, session):
    t = await _ticket(session, world)
    assert (await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr), json={"text": ""})
            ).status_code == 422
    assert (await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr),
                              json={"text": "ok", "add_to_kb": True, "kb_site": "NOPE"})).status_code == 400
    assert (await client.get("/api/tickets/999999999", headers=auth(world.hr))).status_code == 404


async def test_employee_card_lists_tickets(client, world, session):
    t = await _ticket(session, world)
    card = (await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.hr))).json()
    assert card["open_tickets"] == 1 and card["tickets"][0]["id"] == t.id


async def test_message_ids_unique_in_feed(client, world, session, bot):
    t = await _ticket(session, world)
    await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr), json={"text": "Ответ 1"})
    data = (await client.get(f"/api/tickets/{t.id}", headers=auth(world.hr))).json()
    ids = [m["id"] for m in data["messages"]]
    assert len(ids) == len(set(ids))
    assert all(datetime.fromisoformat(m["created_at"]).tzinfo is not None for m in data["messages"])
    assert (await session.scalars(select(Message).where(Message.ticket_id == t.id))).first() is not None
