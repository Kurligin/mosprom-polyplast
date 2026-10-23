"""Диалоги бота на настоящей БД: привязка второго приглашения, красный флаг в любом состоянии,
рассказ о себе без ловушки, /hr и ожидание опроса не стирают друг друга.

Фикстура `world` — tests/surveys/fakes.py: фейковый «МАКС», БД в откатываемой транзакции, синтетика.
"""

import importlib
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.bot import hooks
from app.bot import texts as BT
from app.bot.handlers import S_HR_QUESTION, handle_update
from app.db.models import Employee, Flag, Ticket
from app.scenario import engine
from app.scenario import texts as ST
from app.surveys import engine as surveys
from tests.surveys.fakes import ev_text

pytestmark = pytest.mark.asyncio


def _quiet_import(name: str):
    """Модуль hooks без следа в общем реестре бота: другие тесты (FakeRepo) не должны его видеть."""
    saved = (dict(hooks._callbacks), dict(hooks._states), dict(hooks._commands), list(hooks._pre_routes),
             dict(hooks._single))
    try:
        return importlib.import_module(name)
    finally:
        for target, value in zip((hooks._callbacks, hooks._states, hooks._commands, hooks._pre_routes,
                                  hooks._single), saved, strict=True):
            target.clear()
            target.extend(value) if isinstance(target, list) else target.update(value)


scenario_hooks = _quiet_import("app.scenario.hooks")
signal_hooks = _quiet_import("app.signals.hooks")

RED = "Начальник угрожает меня уволить и орёт, мне страшно"
ABOUT = "Я Анна, пять лет работала технологом на заводе упаковки, люблю походы"
PHOTO = [{"type": "image", "payload": {"url": "https://example.invalid/anna.jpg"}}]


def ev_photo(text: str = "", user_id: int = 100, chat_id: int = 500) -> dict:
    upd = ev_text(text, user_id, chat_id)
    upd["message"]["body"]["attachments"] = PHOTO
    return upd


@pytest.fixture
def routes(world, monkeypatch):
    """Маршрут как в проде: красный флаг (всегда) и рассказ о себе; состояние sc — сценарий."""
    monkeypatch.setattr(hooks, "_pre_routes", [signal_hooks.red_flag_in_message])
    monkeypatch.setitem(hooks._states, "sc", scenario_hooks.scenario_state)
    return world


def _intro_wait(w) -> None:
    w.emp.dialog_data = {**w.emp.dialog_data, engine.INTRO_WAIT: {"since": engine._vnow(w.emp).isoformat(),
                                                                  "asked": []}}


async def _count(w, model, *where) -> int:
    return await w.session.scalar(select(func.count()).select_from(model).where(*where))


async def _red_flags(w) -> list[Flag]:
    return (await w.session.scalars(select(Flag).where(Flag.employee_id == w.emp.id, Flag.level == "red"))).all()


# --- 1. тот же аккаунт открывает приглашение второго новичка ----------------------------------------

async def _second(w, **kw) -> Employee:
    b = Employee(full_name="Второва Белла [тест]", first_name="Белла", site_code="GrPP", work_type="office",
                 start_date=date.today() + timedelta(days=3), recruiter_id=w.rec.id, invite_code="INV-second-b",
                 status="waiting", dialog_data={}, **kw)
    w.session.add(b)
    await w.session.flush()
    return b


async def test_demo_account_rebinds_to_second_invite(world):
    world.emp.is_test = True  # аккаунт ведущего привязан к тестовому новичку
    b = await _second(world)
    await handle_update(world.ctx, world.repo, {"update_type": "bot_started", "chat_id": 500,
                                                "user": {"user_id": 100, "name": "U"}, "payload": "INV-second-b"})
    await world.session.flush()  # без исправления: UniqueViolation на employees.max_user_id
    assert b.max_user_id == 100 and b.chat_id == 500
    assert world.emp.max_user_id is None and world.emp.chat_id is None
    assert "Привет, Белла!" in world.api.text(world.api.last(500))
    assert await world.repo.employee_by_user(100) is b


async def test_real_account_not_rebound_to_second_invite(world):
    b = await _second(world)
    await handle_update(world.ctx, world.repo, {"update_type": "bot_started", "chat_id": 500,
                                                "user": {"user_id": 100, "name": "U"}, "payload": "INV-second-b"})
    await world.session.flush()
    assert b.max_user_id is None and world.emp.max_user_id == 100
    assert world.api.text(world.api.last(500)) == BT.ACCOUNT_TAKEN


# --- 2. красный флаг в любом состоянии --------------------------------------------------------------

async def test_red_flag_after_hr_command(routes):
    w = routes
    await w.say("/hr")
    assert w.emp.dialog_state == S_HR_QUESTION
    await w.say(RED)
    flags = await _red_flags(w)
    assert flags and all(f.source == "message" for f in flags)
    assert await _count(w, Ticket, Ticket.employee_id == w.emp.id, Ticket.source == "hr_command") == 0
    assert w.emp.dialog_state is None, "текст уже у HR, ожидание вопроса снято"
    assert "увидит только HR" in w.api.text(w.api.last(500))


async def test_red_flag_in_hr_command_argument(routes):
    await routes.say("/hr " + RED)
    assert await _red_flags(routes)


async def test_red_flag_when_survey_wait_is_stale(routes):
    w = routes
    mid = await surveys.start_survey(w.repo, w.emp, "week2")
    await w.press("Нет", mid)
    st = {**w.emp.dialog_data["survey"], "wait_at": "2020-01-01T00:00:00+00:00"}  # ждал больше 12 часов
    w.emp.dialog_data = {**w.emp.dialog_data, "survey": st}
    await w.say(RED)
    assert await _red_flags(w), "просроченное ожидание опроса не должно уводить текст мимо флагов"


async def test_red_flag_while_waiting_for_intro(routes):
    w = routes
    w.emp.dialog_state = engine.INTRO_STATE  # как ставила старая версия
    _intro_wait(w)
    await w.say(RED)
    assert await _red_flags(w)
    assert not (w.emp.dialog_data.get("intro") or {}).get("text"), "жалоба не попала в рассказ для коллег"


# --- 3. рассказ о себе: не ловушка ------------------------------------------------------------------

async def test_intro_short_replies_and_questions_go_normal_route(routes, monkeypatch):
    w = routes
    monkeypatch.setattr(hooks, "_pre_routes", [*hooks._pre_routes, scenario_hooks.intro_for_colleagues])
    _intro_wait(w)
    await w.say("спасибо")
    await w.say("Где получить пропуск")
    assert not (w.emp.dialog_data.get("intro") or {}).get("text")
    assert w.emp.dialog_data.get("last_question") == "Где получить пропуск", "вопрос ушёл обычным маршрутом"
    assert engine.intro_wait(w.emp), "ожидание рассказа не потерялось"

    await w.say(ABOUT)
    assert w.api.text(w.api.last(500)) == ST.INTRO_NEED_PHOTO[0]
    n = len(w.api.sent(500))
    await w.say("Ещё умею настраивать экструдер и читать техкарты")
    assert len(w.api.sent(500)) == n, "«пришли фото» — один раз"
    await handle_update(w.ctx, w.repo, ev_photo())
    intro = w.emp.dialog_data["intro"]
    assert intro["text"].startswith(ABOUT) and intro["photo_url"] == PHOTO[0]["payload"]["url"]
    assert w.api.text(w.api.last(500)) == ST.INTRO_THANKS[0] and engine.intro_wait(w.emp) is None
    group = w.api.text(w.api.last(900))
    assert "Опросова Анна, GrPP" in group and "[тест]" not in group and "прислал" not in group


async def test_intro_wait_expires(routes, monkeypatch):
    w = routes
    monkeypatch.setattr(hooks, "_pre_routes", [*hooks._pre_routes, scenario_hooks.intro_for_colleagues])
    w.emp.dialog_data = {**w.emp.dialog_data,
                         engine.INTRO_WAIT: {"since": "2020-01-01T00:00:00+00:00", "asked": []}}
    await w.say(ABOUT)
    assert not (w.emp.dialog_data.get("intro") or {}).get("text"), "через 48 часов текст — обычный вопрос"
    assert engine.INTRO_WAIT not in w.emp.dialog_data


# --- 6. опрос, /hr и рассказ о себе не стирают друг друга ------------------------------------------------

async def test_survey_and_hr_do_not_erase_intro_wait(routes, monkeypatch):
    w = routes
    monkeypatch.setattr(hooks, "_pre_routes", [*hooks._pre_routes, scenario_hooks.intro_for_colleagues])
    _intro_wait(w)
    mid = await surveys.start_survey(w.repo, w.emp, "day3")
    await w.press("Возникли трудности с документами", mid)
    await w.say("Не дали копию трудового договора")
    assert w.emp.dialog_data["survey"]["q"] == 1, "ответ ушёл в опрос"
    await w.say("/hr")
    await w.say("Когда выдадут пропуск на парковку?")
    await handle_update(w.ctx, w.repo, ev_photo())  # фото без подписи после опроса и /hr
    assert (w.emp.dialog_data.get("intro") or {}).get("photo_url"), "фото не потерялось"
    assert w.api.text(w.api.last(500)) == ST.INTRO_NEED_TEXT[0]


async def test_hr_question_keeps_survey_wait(routes):
    w = routes
    mid = await surveys.start_survey(w.repo, w.emp, "week2")
    await w.press("Нет", mid)
    survey_state = w.emp.dialog_state
    assert survey_state == "sv:week2:0:reason"
    await w.say("/hr")
    await w.say("Можно перенести отпуск?")
    assert w.emp.dialog_state == survey_state, "после вопроса HR опрос снова ждёт ответ"
    await w.say("Не хватает инструкции по переналадке")
    assert w.emp.dialog_data["survey"]["q"] == 1


async def test_photo_without_text_is_not_silently_dropped(routes):
    await handle_update(routes.ctx, routes.repo, ev_photo())
    assert routes.api.text(routes.api.last(500)) == BT.NON_TEXT[0]
