"""Сценарии обработчиков бота на фейковом API и фейковой БД."""

import pytest

from app.bot import texts as T
from app.bot.handlers import S_CONSENT, S_FORM, S_HR_QUESTION, handle_update
from tests.bot.conftest import ev_cb, ev_started, ev_text

pytestmark = pytest.mark.asyncio


async def test_start_without_code_asks_code(ctx, repo, api):
    await handle_update(ctx, repo, ev_started(payload=None))
    assert api.sent[-1]["text"] == T.ASK_CODE


async def test_unknown_code(ctx, repo, api):
    await handle_update(ctx, repo, ev_started(payload="NOPE-123"))
    assert api.sent[-1]["text"] == T.CODE_NOT_FOUND


def _answer_buttons(answer) -> list[str]:
    atts = answer["message"]["attachments"] or []
    return [b["payload"] for a in atts for row in a["payload"]["buttons"] for b in row]


async def test_full_onboarding_is_one_message(ctx, repo, api):
    emp = repo.add_employee()
    await handle_update(ctx, repo, ev_started(payload="INV-anna1"))
    assert emp.max_user_id == 100 and emp.chat_id == 500
    assert emp.dialog_state == S_FORM
    assert "Анна" in api.sent[-1]["text"] and T.ASK_FORM in api.sent[-1]["text"]
    assert api.buttons() == ["ob:ty", "ob:vy"]
    sent_before = len(api.sent)

    await handle_update(ctx, repo, ev_cb("ob:ty"))
    assert emp.address_form == "ty" and emp.dialog_state == S_CONSENT
    assert api.answers[-1]["message"]["text"] == T.CONSENT[0]
    assert _answer_buttons(api.answers[-1]) == ["ob:consent", "ob:info"]

    await handle_update(ctx, repo, ev_cb("ob:info"))
    assert T.CONSENT_INFO[0] in api.answers[-1]["message"]["text"]
    assert _answer_buttons(api.answers[-1]) == ["ob:consent"]

    await handle_update(ctx, repo, ev_cb("ob:consent"))
    assert [c.purpose for c in repo.consents] == ["adaptation"]
    assert _answer_buttons(api.answers[-1]) == ["ob:ai_yes", "ob:ai_no"]

    await handle_update(ctx, repo, ev_cb("ob:ai_no"))
    assert repo.consents[-1].purpose == "ai_analysis" and repo.consents[-1].given is False
    assert emp.status == "active" and emp.dialog_state is None and emp.dialog_data["onboarded"]
    final = api.answers[-1]["message"]
    assert final["text"].startswith("Очень приятно с тобой познакомиться, Анна!")
    assert final["attachments"] == []
    # всё знакомство после первого сообщения — правка на месте, новых сообщений нет
    assert len(api.sent) == sent_before


async def test_test_account_takes_name_from_profile(ctx, repo, api):
    emp = repo.add_employee(is_test=True, first_name="Тест", full_name="Тестовый Новичок")
    upd = ev_started(payload="INV-anna1")
    upd["user"].update({"first_name": "Иван", "last_name": "Петров"})
    await handle_update(ctx, repo, upd)
    assert emp.first_name == "Иван" and emp.full_name == "Иван Петров"
    assert "Привет, Иван!" in api.sent[-1]["text"]


async def test_text_code_fallback(ctx, repo, api):
    emp = repo.add_employee()
    await handle_update(ctx, repo, ev_text("привет"))
    assert api.sent[-1]["text"] == T.ASK_CODE
    await handle_update(ctx, repo, ev_text("INV-anna1"))
    assert emp.max_user_id == 100 and emp.dialog_state == S_FORM


async def test_text_during_onboarding_points_to_buttons(ctx, repo, api):
    repo.add_employee(max_user_id=100, chat_id=500, dialog_state=S_CONSENT)
    await handle_update(ctx, repo, ev_text("а что дальше?"))
    assert api.sent[-1]["text"] == T.PRESS_BUTTON[1]


async def test_unknown_button_is_closed(ctx, repo, api):
    repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, ev_cb("zz:1"))
    assert api.answers[-1]["notification"] == "Принято"


async def test_staff_binding_with_panel(ctx, repo, api):
    ctx.miniapp_url = "https://example.test/app"
    staff = repo.add_staff()
    await handle_update(ctx, repo, ev_started(payload="STF-olga1"))
    assert staff.max_user_id == 100
    assert "вы подключены как HR" in api.sent[-1]["text"]
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
    assert api.sent[-2]["chat_id"] == -900
    assert api.sent[-1]["text"] == T.HR_DONE[0]
    assert emp.dialog_state is None


async def test_hr_command_with_question_in_same_line(ctx, repo, api):
    emp = repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, ev_text("/hr Когда придёт первая зарплата"))
    t = repo.tickets[-1]
    assert t.source == "hr_command" and t.text == "Когда придёт первая зарплата"
    assert api.sent[-1]["text"] == T.HR_DONE[0] and emp.dialog_state is None


async def test_hr_question_has_exits(ctx, repo, api):
    emp = repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, ev_text("/hr"))
    assert api.buttons() == ["hr:cancel"]
    # «ок» и «спасибо» — не вопрос: обращение не создаём, ждём дальше
    await handle_update(ctx, repo, ev_text("ок"))
    await handle_update(ctx, repo, ev_text("Спасибо!"))
    assert repo.tickets == [] and emp.dialog_state == S_HR_QUESTION
    # кнопка «Отмена» — ожидание снято, сообщение меняется на месте
    await handle_update(ctx, repo, ev_cb("hr:cancel"))
    assert emp.dialog_state is None and api.answers[-1]["message"]["text"] == T.HR_CANCELLED[0]
    await handle_update(ctx, repo, ev_text("Где взять спецодежду?"))
    assert repo.tickets == [] and api.buttons() == ["hr:forward"], "вопрос ушёл обычным маршрутом"
    # «отмена» текстом
    await handle_update(ctx, repo, ev_text("/hr"))
    await handle_update(ctx, repo, ev_text("не надо"))
    assert emp.dialog_state is None and api.sent[-1]["text"] == T.HR_CANCELLED[0] and repo.tickets == []


async def test_hr_question_expires(ctx, repo, api):
    emp = repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, ev_text("/hr"))
    emp.dialog_data = {**emp.dialog_data, "hr_wait": {"at": "2020-01-01T00:00:00+00:00", "resume": None}}
    await handle_update(ctx, repo, ev_text("Где взять спецодежду?"))
    assert repo.tickets == [] and emp.dialog_state is None, "через полчаса это обычный вопрос"
    assert api.buttons() == ["hr:forward"]


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
    # «Передать HR?» превратилось в «Передал HR» в том же сообщении
    assert api.answers[-1]["message"]["text"] == T.HR_DONE[0]
    # повторное нажатие той же кнопки: второго обращения нет, «Передал HR» не затирается
    await handle_update(ctx, repo, ev_cb("hr:forward"))
    assert len(repo.tickets) == 1
    assert api.answers[-1]["message"] is None and api.answers[-1]["notification"] == T.ALREADY_DONE


# bot_added / bot_removed: группа больше не становится HR-группой сама, её выбирает HR кнопкой.
# Проверки — tests/chats/test_chats_bot.py (чаты хранятся в БД, фейковому репозиторию не под силу).


async def test_bot_stopped(ctx, repo, api):
    emp = repo.add_employee(max_user_id=100, chat_id=500, status="active", address_form="ty")
    await handle_update(ctx, repo, {"update_type": "bot_stopped", "chat_id": 500, "user": {"user_id": 100}})
    assert emp.status == "stopped"
