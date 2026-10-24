"""Демо-режим в чате: /demo меняет роль без кодов, сбрасывает прогресс и перематывает время тестового новичка.

Фикстура `world` — tests/surveys/fakes.py: фейковый «МАКС», БД в откатываемой транзакции, синтетика.
Новичок `world.emp` (аккаунт 100) — без пометок is_test / is_synthetic, то есть «настоящий».
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app.bot import texts as BT
from app.bot.handlers import S_FORM, handle_update
from app.config import settings
from app.db.models import Consent, Employee, Message, Site, Staff, Ticket
from seed.seed import STAFF
from tests.surveys.fakes import ev_press, ev_text

pytestmark = pytest.mark.asyncio

ME, MY_CHAT = 4242, 4343  # ведущий демо: аккаунт ещё ни к кому не привязан


@pytest_asyncio.fixture
async def demo(world, monkeypatch):
    """Демо включено, тестовые новички GTP и Лужники, демо-персонал с ФИО из сида.

    Демо-данные, которые уже лежат в базе разработки, на время теста убираем из выборки (транзакция откатится).
    """
    monkeypatch.setattr(settings, "demo_enabled", True)
    s = world.session
    await s.execute(update(Employee).where(Employee.is_test).values(is_test=False))
    names = [name for name, _role, _site in STAFF]
    await s.execute(update(Staff).where(Staff.full_name.in_(names))
                    .values(full_name=func.concat(Staff.full_name, " [старое]")))
    if await s.get(Site, "Luzhniki") is None:
        s.add(Site(code="Luzhniki", name="Лужники", tz="Europe/Moscow"))
    await s.flush()
    start = date.today() + timedelta(days=2)
    world.gtp = Employee(full_name="Тестовый Новичок GTP", first_name="Тест", site_code="GTP",
                         work_type="production", start_date=start, probation_end=start + timedelta(days=90),
                         manager_id=world.mgr.id, recruiter_id=world.rec.id, is_test=True)
    world.luzh = Employee(full_name="Тестовый Новичок Лужники", first_name="Тест", site_code="Luzhniki",
                          work_type="office", start_date=start, probation_end=start + timedelta(days=90),
                          manager_id=world.mgr.id, recruiter_id=world.rec.id, is_test=True)
    s.add_all([world.gtp, world.luzh])
    world.demo_staff = {}
    for name, role, site in STAFF:
        st = Staff(full_name=name, role=role, site_code=site)
        s.add(st)
        world.demo_staff[role] = st
    await s.flush()
    return world


async def _cmd(w, text: str, user_id: int = ME, chat_id: int = MY_CHAT) -> str:
    await handle_update(w.ctx, w.repo, ev_text(text, user_id=user_id, chat_id=chat_id))
    await w.session.flush()
    return w.api.last(chat_id)


async def _press(w, mid: str, label: str, user_id: int = ME, chat_id: int = MY_CHAT) -> None:
    await handle_update(w.ctx, w.repo, ev_press(w.api, mid, label, user_id=user_id, chat_id=chat_id))
    await w.session.flush()


# --- включено / выключено ----------------------------------------------------------------------------------

async def test_demo_command_shows_roles(demo):
    mid = await _cmd(demo, "/demo")
    assert demo.api.text(mid) == BT.DEMO_MENU
    labels = [t for t, _ in demo.api.buttons(mid)]
    assert labels == list(BT.DEMO_ROLES.values())
    assert BT.DEMO_SHIFT not in labels and BT.DEMO_RESET not in labels, "перемотка — только у тестового новичка"


async def test_demo_disabled_is_unknown_command(world, monkeypatch):
    monkeypatch.setattr(settings, "demo_enabled", False)
    mid = await _cmd(world, "/demo")
    assert world.api.text(mid) == BT.ASK_CODE, "не привязан: как любое сообщение без кода"
    mid = await _cmd(world, "/demo", user_id=100, chat_id=500)
    assert world.api.text(mid).startswith(BT.tv(world.emp, BT.UNKNOWN_COMMAND))
    assert not any((p or "").startswith("dm:") for _, p in world.api.buttons(mid))


async def test_demo_button_ignored_when_disabled(demo, monkeypatch):
    mid = await _cmd(demo, "/demo")
    monkeypatch.setattr(settings, "demo_enabled", False)
    await _press(demo, mid, "HR")
    assert BT.DEMO_OFF in demo.api.toasts
    assert demo.demo_staff["hr"].max_user_id is None


async def test_start_without_code_in_demo_shows_menu(demo):
    await handle_update(demo.ctx, demo.repo, {"update_type": "bot_started", "chat_id": MY_CHAT,
                                              "user": {"user_id": ME, "name": "U"}, "payload": None})
    assert demo.api.text(demo.api.last(MY_CHAT)) == BT.DEMO_MENU


async def test_start_without_code_no_demo_asks_code(world, monkeypatch):
    monkeypatch.setattr(settings, "demo_enabled", False)
    await handle_update(world.ctx, world.repo, {"update_type": "bot_started", "chat_id": MY_CHAT,
                                                "user": {"user_id": ME, "name": "U"}, "payload": None})
    assert world.api.text(world.api.last(MY_CHAT)) == BT.ASK_CODE


# --- смена роли ---------------------------------------------------------------------------------------------

async def test_switch_staff_then_newcomer_unbinds_previous(demo):
    w = demo
    mid = await _cmd(w, "/demo")
    await _press(w, mid, "HR")
    hr = w.demo_staff["hr"]
    assert hr.max_user_id == ME and hr.chat_id == MY_CHAT
    assert w.api.text(mid).startswith(BT.STAFF_BOUND.format(role="HR")), "ответ в том же сообщении"
    assert len(w.api.sent(MY_CHAT)) == 1, "бот не флудит"

    mid = await _cmd(w, "/demo")
    assert w.api.text(mid).startswith(BT.DEMO_NOW.format(role="HR"))
    await _press(w, mid, "Руководитель")
    assert hr.max_user_id is None and hr.chat_id is None
    assert w.demo_staff["manager"].max_user_id == ME

    mid = await _cmd(w, "/demo")
    await _press(w, mid, "Новичок GTP")
    assert w.demo_staff["manager"].max_user_id is None
    assert w.gtp.max_user_id == ME and w.gtp.chat_id == MY_CHAT
    assert w.gtp.dialog_state == S_FORM and w.gtp.dialog_data["onboard_mid"] == mid
    assert w.api.text(mid).startswith(BT.WELCOME.format(name="Тест")), "знакомство в том же сообщении"

    mid = await _cmd(w, "/demo")
    labels = [t for t, _ in w.api.buttons(mid)]
    assert BT.DEMO_SHIFT in labels and BT.DEMO_RESET in labels
    await _press(w, mid, "Новичок Лужники")
    assert w.gtp.max_user_id is None and w.luzh.max_user_id == ME


async def test_switch_takes_role_from_other_account(demo):
    w = demo
    w.gtp.max_user_id, w.gtp.chat_id = 5555, 5556
    await w.session.flush()
    mid = await _cmd(w, "/demo")
    await _press(w, mid, "Новичок GTP")
    assert w.gtp.max_user_id == ME and w.gtp.chat_id == MY_CHAT
    assert await w.repo.employee_by_user(5555) is None, "прежний аккаунт просто отвязан"


async def test_real_newcomer_account_not_switched(demo):
    w = demo
    mid = await _cmd(w, "/demo", user_id=100, chat_id=500)
    await handle_update(w.ctx, w.repo, ev_press(w.api, mid, "HR", user_id=100, chat_id=500))
    await w.session.flush()
    assert w.api.text(mid) == BT.DEMO_REAL
    assert w.emp.max_user_id == 100 and w.emp.chat_id == 500, "настоящего новичка не отвязали"
    assert w.demo_staff["hr"].max_user_id is None


async def test_real_staff_account_not_switched(demo):
    w = demo
    real = Staff(full_name="Настоящая Анна (синтетика теста)", role="hr", max_user_id=6000, chat_id=6001)
    w.session.add(real)
    await w.session.flush()
    mid = await _cmd(w, "/demo", user_id=6000, chat_id=6001)
    await _press(w, mid, "Новичок GTP", user_id=6000, chat_id=6001)
    assert w.api.text(mid) == BT.DEMO_REAL
    assert real.max_user_id == 6000 and w.gtp.max_user_id is None


async def test_real_people_never_become_demo_targets(demo):
    """Роль «Новичок GTP» — только тестовый новичок, «HR» — только демо-сотрудник из сида."""
    w = demo
    w.gtp.is_test = False
    w.demo_staff["hr"].full_name = "Соколова Анна Сергеевна"
    await w.session.flush()
    mid = await _cmd(w, "/demo")
    await _press(w, mid, "Новичок GTP")
    assert w.api.text(mid) == BT.DEMO_NO_PERSON.format(role="Новичок GTP")
    await _press(w, mid, BT.DEMO_BACK)
    await _press(w, mid, "HR")
    assert w.api.text(mid) == BT.DEMO_NO_PERSON.format(role="HR")
    assert w.gtp.max_user_id is None and w.demo_staff["hr"].max_user_id is None


# --- сброс прогресса ----------------------------------------------------------------------------------------

async def _count(w, model) -> int:
    return await w.session.scalar(select(func.count()).select_from(model).where(model.employee_id == w.gtp.id))


async def test_reset_progress_restarts_onboarding(demo):
    w = demo
    mid = await _cmd(w, "/demo")
    await _press(w, mid, "Новичок GTP")
    g = w.gtp
    await w.repo.add_consent(g.id, "adaptation", True)
    await w.repo.create_ticket(employee_id=g.id, source="hr_command", text="вопрос")
    g.address_form, g.status, g.time_shift_hours, g.pending_question_id = "ty", "active", 48, 7
    g.dialog_state, g.dialog_data = "sv:1:2", {"onboarded": True, "survey": {"mid": "x"}}
    await w.session.flush()
    assert await _count(w, Consent) and await _count(w, Ticket) and await _count(w, Message)

    mid = await _cmd(w, "/demo")
    await _press(w, mid, BT.DEMO_RESET)
    for model in (Consent, Ticket):
        assert await _count(w, model) == 0
    assert g.address_form is None and g.pending_question_id is None and g.time_shift_hours == 0
    assert g.status == "waiting"
    assert g.dialog_state == S_FORM and g.dialog_data == {"onboard_mid": mid}
    assert g.max_user_id == ME, "привязка сохранилась"
    assert w.api.text(mid).startswith(BT.WELCOME.format(name="Тест"))


async def test_reset_only_for_test_newcomer(demo):
    w = demo
    mid = await _cmd(w, "/demo")
    await _press(w, mid, "HR")
    # старое меню с кнопкой сброса: от имени HR сброс не выполняется
    w.api.msgs[mid]["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": BT.DEMO_RESET, "payload": "dm:reset"}]]}}]
    await _press(w, mid, BT.DEMO_RESET)
    assert w.api.text(mid) == BT.DEMO_ONLY_TEST


# --- перемотка ----------------------------------------------------------------------------------------------

async def test_shift_uses_time_machine(demo, monkeypatch):
    from app.scenario import engine

    calls = []

    async def fake(session, emp, to):
        calls.append((emp.id, to))
        return {"employee_id": emp.id, "day": 3, "message": "Тест: день 3"}

    monkeypatch.setattr(engine, "time_travel", fake)
    w = demo
    mid = await _cmd(w, "/demo")
    await _press(w, mid, "Новичок GTP")
    mid = await _cmd(w, "/demo")
    await _press(w, mid, BT.DEMO_SHIFT)
    assert w.api.text(mid) == BT.DEMO_SHIFT_ASK
    assert [t for t, _ in w.api.buttons(mid)] == [*BT.DEMO_SHIFT_LABELS.values(), BT.DEMO_BACK]
    await _press(w, mid, "3")
    assert calls == [(w.gtp.id, "3")]
    assert w.api.text(mid) == "Тест: день 3"
    await _press(w, mid, BT.DEMO_BACK)
    assert BT.DEMO_MENU in w.api.text(mid)


async def test_demo_newcomer_takes_name_from_max_profile(demo):
    """Как при входе по коду: тестовый новичок получает имя из профиля «МАКС», а не «Тестовый Новичок GTP»."""
    mid = await _cmd(demo, "/demo")
    ev = ev_press(demo.api, mid, BT.DEMO_ROLES["emp:GTP"], user_id=ME, chat_id=MY_CHAT)
    ev["callback"]["user"].update({"first_name": "Иван", "last_name": "Петров", "name": "Иван Петров"})
    await handle_update(demo.ctx, demo.repo, ev)
    await demo.session.flush()
    await demo.session.refresh(demo.gtp)
    assert demo.gtp.max_user_id == ME
    assert (demo.gtp.full_name, demo.gtp.first_name) == ("Иван Петров", "Иван")
