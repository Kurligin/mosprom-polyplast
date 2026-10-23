"""Сценарии обработчиков бота на фейковом API и фейковой БД."""

import pytest

from app.bot import texts as T
from app.bot.handlers import S_CONSENT, handle_update
from tests.bot.conftest import ev_cb, ev_started, ev_text

pytestmark = pytest.mark.asyncio


async def test_start_without_code_asks_code(ctx, repo, api):
    await handle_update(ctx, repo, ev_started(payload=None))
    assert api.sent[-1]["text"] == T.ASK_CODE


async def test_unknown_code(ctx, repo, api):
    await handle_update(ctx, repo, ev_started(payload="NOPE-123"))
    assert api.sent[-1]["text"] == T.CODE_NOT_FOUND


async def test_full_onboarding(ctx, repo, api):
    emp = repo.add_employee()
    await handle_update(ctx, repo, ev_started(payload="INV-anna1"))
    assert emp.max_user_id == 100 and emp.chat_id == 500
    assert emp.dialog_state == S_CONSENT
    assert "Анна" in api.sent[-1]["text"]
    assert api.buttons() == ["consent:yes", "consent:info"]

    await handle_update(ctx, repo, ev_cb("consent:info"))
    assert api.sent[-1]["text"] == T.CONSENT_INFO
    assert api.buttons() == ["consent:yes"]

    await handle_update(ctx, repo, ev_cb("consent:yes"))
    assert api.answers[-1]["message"]["attachments"] == []
    assert "✅ Согласен" in api.answers[-1]["message"]["text"]
    assert [c.purpose for c in repo.consents] == ["adaptation"]
    assert api.buttons() == ["ai:yes", "ai:no"]

    await handle_update(ctx, repo, ev_cb("ai:no"))
    assert repo.consents[-1].purpose == "ai_analysis" and repo.consents[-1].given is False
    assert api.sent[-1]["text"] == T.ASK_FORM

    await handle_update(ctx, repo, ev_cb("form:ty"))
    assert emp.address_form == "ty" and emp.status == "active" and emp.dialog_state is None
    assert any(m["text"] == "Очень приятно с тобой познакомиться, Анна!" for m in api.sent)
    # все исходящие записаны в messages
    assert sum(1 for m in repo.messages if m.direction == "out") == len(api.sent)


async def test_text_code_fallback(ctx, repo, api):
    emp = repo.add_employee()
    await handle_update(ctx, repo, ev_text("привет"))
    assert api.sent[-1]["text"] == T.ASK_CODE
    await handle_update(ctx, repo, ev_text("INV-anna1"))
    assert emp.max_user_id == 100 and emp.dialog_state == S_CONSENT


async def test_text_during_onboarding_repeats_question(ctx, repo, api):
    repo.add_employee(max_user_id=100, chat_id=500, dialog_state=S_CONSENT)
    await handle_update(ctx, repo, ev_text("а что дальше?"))
    assert api.sent[-2]["text"] == T.PRESS_BUTTON
    assert api.buttons() == ["consent:yes", "consent:info"]


async def test_staff_binding_with_panel(ctx, repo, api):
    ctx.miniapp_url = "https://example.test/app"
    staff = repo.add_staff()
    await handle_update(ctx, repo, ev_started(payload="STF-olga1"))
    assert staff.max_user_id == 100
    assert "Вы подключены как HR" in api.sent[-1]["text"]
    btn = api.sent[-1]["attachments"][0]["payload"]["buttons"][0][0]
    assert btn == {"type": "open_app", "text": "Открыть панель", "web_app": "korsik_test_bot"}


async def test_hr_command_creates_ticket_and_notifies_group(ctx, repo, api):
    emp = repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    repo.settings["hr_group_chat_id"] = -900
    await handle_update(ctx, repo, ev_text("/hr"))
    assert api.sent[-1]["text"] == T.HR_ASK[0]
    await handle_update(ctx, repo, ev_text("Когда выдадут пропуск?"))
    t = repo.tickets[-1]
    assert t.source == "hr_command" and t.status == "new" and t.employee_id == emp.id
    assert t.text == "Когда выдадут пропуск?"
    assert api.sent[-2]["text"] == T.HR_DONE[0]
    assert api.sent[-1]["chat_id"] == -900
    assert emp.dialog_state is None


async def test_food_stub_vy(ctx, repo, api):
    repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="vy")
    await handle_update(ctx, repo, ev_text("/food"))
    assert api.sent[-1]["text"] == T.FOOD_STUB[1]


async def test_free_text_then_forward(ctx, repo, api):
    repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, ev_text("Где взять спецодежду?"))
    assert api.buttons() == ["hr:forward"]
    await handle_update(ctx, repo, ev_cb("hr:forward"))
    assert repo.tickets[-1].source == "unknown"
    assert repo.tickets[-1].text == "Где взять спецодежду?"


async def test_bot_added_saves_group(ctx, repo, api):
    await handle_update(ctx, repo, {"update_type": "bot_added", "chat_id": -777, "user": {"user_id": 1},
                                    "is_channel": False})
    assert repo.settings["hr_group_chat_id"] == -777


async def test_bot_stopped(ctx, repo, api):
    emp = repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, {"update_type": "bot_stopped", "chat_id": 500, "user": {"user_id": 100}})
    assert emp.status == "stopped"
