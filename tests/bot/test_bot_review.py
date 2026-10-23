"""Диалоги бота: случаи из ревью. Рассказ о себе не копит чужие реплики и вопросы, повторные нажатия не затирают
сообщения, неизвестные команды не становятся обращениями, привязка второй роли к тому же аккаунту.

Фикстура `world` — tests/surveys/fakes.py: фейковый «МАКС», БД в откатываемой транзакции, синтетика.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.bot import hooks
from app.bot import texts as BT
from app.bot.handlers import S_HR_QUESTION, handle_update
from app.db.models import Staff, Ticket
from app.scenario import engine
from app.scenario import texts as ST
from tests.bot.test_bot_dialogs import ABOUT, ev_photo, scenario_hooks, signal_hooks
from tests.surveys.fakes import ev_press

pytestmark = pytest.mark.asyncio


@pytest.fixture
def intro(world, monkeypatch):
    """Маршрут как в проде: красный флаг, рассказ о себе (pre_route), ожидание рассказа открыто."""
    monkeypatch.setattr(hooks, "_pre_routes", [signal_hooks.red_flag_in_message,
                                               scenario_hooks.intro_for_colleagues])
    monkeypatch.setitem(hooks._states, "sc", scenario_hooks.scenario_state)
    world.emp.dialog_data = {**world.emp.dialog_data,
                             engine.INTRO_WAIT: {"since": engine._vnow(world.emp).isoformat(), "asked": []}}
    return world


async def tickets(w) -> int:
    return await w.session.scalar(select(func.count()).select_from(Ticket).where(Ticket.employee_id == w.emp.id))


# --- 2. после рассказа ждём только фото ------------------------------------------------------------------

async def test_after_story_other_texts_are_not_glued_into_it(intro):
    w = intro
    await w.say(ABOUT)
    assert w.api.text(w.api.last(500)) == ST.INTRO_NEED_PHOTO[0]
    for text in ("Сейчас найду фото, минутку", "Не могу найти, где получить спецодежду"):
        n = len(w.api.sent(500))
        await w.say(text)
        assert len(w.api.sent(500)) == n + 1, f"«{text}»: бот отвечает, а не глотает молча"
        assert w.emp.dialog_data.get("last_question") == text, "реплика ушла обычным маршрутом"
    assert w.emp.dialog_data["intro"]["text"] == ABOUT, "в рассказ для коллег ничего не дописано"
    assert engine.intro_wait(w.emp), "фото всё ещё ждём"
    await handle_update(w.ctx, w.repo, ev_photo())
    assert w.emp.dialog_data["intro"]["text"] == ABOUT and w.emp.dialog_data["intro"]["photo_url"]


async def test_second_photo_still_gets_reply(intro):
    w = intro
    await handle_update(w.ctx, w.repo, ev_photo())
    n = len(w.api.sent(500))
    await handle_update(w.ctx, w.repo, ev_photo())
    assert len(w.api.sent(500)) == n + 1, "без ответа новичку не остаёмся"
    assert w.api.text(w.api.last(500)) == ST.INTRO_NEED_TEXT[0]


# --- 16. вопрос без «?» в ожидании рассказа -------------------------------------------------------------

async def test_question_without_mark_is_not_a_story(intro):
    w = intro
    await w.say("Как получить пропуск на территорию завода")
    assert not (w.emp.dialog_data.get("intro") or {}).get("text")
    assert w.emp.dialog_data.get("last_question") == "Как получить пропуск на территорию завода"
    await w.say("Скажите, пожалуйста, где тут столовая")
    assert not (w.emp.dialog_data.get("intro") or {}).get("text")
    assert engine.intro_wait(w.emp)


# --- 17. повторное нажатие не затирает обновлённое сообщение -------------------------------------------------

async def test_double_tap_in_onboarding_keeps_greeting(world):
    w = world
    w.emp.dialog_data = {}
    w.emp.status = "waiting"
    w.emp.dialog_state = "ob:ai"
    await w.session.flush()
    await handle_update(w.ctx, w.repo, {"update_type": "message_created", "message": {
        "sender": {"user_id": 100, "name": "U"}, "recipient": {"chat_id": 500, "chat_type": "dialog"},
        "body": {"mid": "in-start", "text": "/start"}}})
    mid = w.api.last(500)
    stale = ev_press(w.api, mid, BT.AI_YES)  # снимок сообщения до первого нажатия
    await handle_update(w.ctx, w.repo, ev_press(w.api, mid, BT.AI_YES))
    greeting = w.api.text(mid)
    assert greeting.startswith("Очень приятно")
    await handle_update(w.ctx, w.repo, stale)
    assert w.api.text(mid) == greeting, "второе нажатие не вернуло «✅ Можно» вместо знакомства"


async def test_hr_cancel_after_question_sent(world):
    w = world
    await w.say("/hr")
    ask = w.api.last(500)
    assert ("Отмена", "hr:cancel") in w.api.buttons(ask)
    await w.say("Когда выдадут пропуск на парковку?")
    assert w.api.buttons(ask) == [], "вопрос у HR: кнопку «Отмена» убрали"
    stale = {"update_type": "message_callback",
             "callback": {"callback_id": f"{ask}|late", "payload": "hr:cancel", "user": {"user_id": 100}},
             "message": {"recipient": {"chat_id": 500, "chat_type": "dialog"},
                         "body": {"mid": ask, "text": BT.HR_ASK[0], "attachments": []}}}
    before = w.api.text(ask)
    await handle_update(w.ctx, w.repo, stale)
    assert w.api.text(ask) == before and "✅" not in w.api.text(ask)
    assert w.api.toasts, "на старое нажатие — всплывашка"


# --- 18. неизвестная команда ------------------------------------------------------------------------------

async def test_cancel_command_cancels_hr_question(world):
    w = world
    await w.say("/hr")
    await w.say("/cancel")
    assert await tickets(w) == 0, "«/cancel» не стал обращением"
    assert w.emp.dialog_state != S_HR_QUESTION
    assert w.api.text(w.api.last(500)) == BT.HR_CANCELLED[0]


async def test_unknown_command_lists_commands(world):
    w = world
    await w.say("/help")
    assert await tickets(w) == 0 and not w.emp.dialog_data.get("last_question")
    text = w.api.text(w.api.last(500))
    assert "/hr" in text and "/food" in text


async def test_unknown_command_is_not_survey_answer(world):
    w = world
    from app.surveys import engine as surveys

    mid = await surveys.start_survey(w.repo, w.emp, "week2")
    await w.press("Нет", mid)
    await w.say("/отмена")
    assert w.emp.dialog_data["survey"]["q"] == 0, "команда не записана ответом опроса"


# --- 15. вторая роль на том же аккаунте ------------------------------------------------------------------------

async def _staff(w, role: str, code: str, **kw) -> Staff:
    st = Staff(full_name=f"{role} (тест)", role=role, bind_code=code, **kw)
    w.session.add(st)
    await w.session.flush()
    return st


async def _bind(w, user_id: int, code: str) -> None:
    await handle_update(w.ctx, w.repo, {"update_type": "bot_started", "chat_id": 700,
                                        "user": {"user_id": user_id, "name": "U"}, "payload": code})
    await w.session.flush()


async def test_staff_account_rebinds_to_second_role_in_demo(world, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "demo_enabled", True)
    hr = await _staff(world, "hr", "STF-hr-1")
    mgr = await _staff(world, "manager", "STF-mgr-1")
    await _bind(world, 777, "STF-hr-1")
    await _bind(world, 777, "STF-mgr-1")  # без исправления: UniqueViolation на staff.max_user_id после «Готово»
    assert mgr.max_user_id == 777 and hr.max_user_id is None
    assert "руководитель" in world.api.text(world.api.last(700))


async def test_staff_account_not_rebound_without_demo(world, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "demo_enabled", False)
    hr = await _staff(world, "hr", "STF-hr-2")
    mgr = await _staff(world, "manager", "STF-mgr-2")
    await _bind(world, 778, "STF-hr-2")
    await _bind(world, 778, "STF-mgr-2")
    assert hr.max_user_id == 778 and mgr.max_user_id is None
    assert world.api.text(world.api.last(700)) == BT.ACCOUNT_TAKEN


async def test_newcomer_account_bound_as_hr_gets_hr_buttons(world):
    """Аккаунт тестового новичка привязан и как HR: кнопки HR (ch, tk, mg) работают от имени HR."""
    w = world
    w.emp.is_test = True
    await _staff(w, "hr", "STF-hr-3")
    await _bind(w, 100, "STF-hr-3")
    seen = {}

    async def handler(cb, person):
        seen["person"], seen["is_staff"] = person, cb.is_staff
        await cb.toast("ok")

    hooks._callbacks["tk"] = handler
    try:
        await handle_update(w.ctx, w.repo, {
            "update_type": "message_callback",
            "callback": {"callback_id": "x|1", "payload": "tk:take:1", "user": {"user_id": 100}},
            "message": {"recipient": {"chat_id": 900, "chat_type": "chat"}, "body": {"mid": "g1", "text": "t"}}})
    finally:
        hooks._callbacks.pop("tk", None)
    assert seen["is_staff"] and isinstance(seen["person"], Staff)



async def test_real_newcomer_account_is_not_bound_as_staff(world):
    await _staff(world, "hr", "STF-hr-4")
    await handle_update(world.ctx, world.repo, {"update_type": "bot_started", "chat_id": 500,
                                                "user": {"user_id": 100, "name": "U"}, "payload": "STF-hr-4"})
    await world.session.flush()
    assert await world.repo.staff_by_user(100) is None
    assert world.api.text(world.api.last(500)) == BT.ACCOUNT_TAKEN
