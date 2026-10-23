"""Обращения: уведомление HR, окно ответа 30 минут, кнопка «Взять себе» в HR-группе."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.bot import hooks
from app.bot.repo import SqlRepo
from app.db.models import Message, Ticket
from app.tickets import service
from tests.api.conftest import buttons

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ticket_hooks():
    """Регистрация хуков обращений только на время теста: реестр бота общий для всех тестов."""
    import importlib

    import app.tickets.hooks as th

    saved = (dict(hooks._single), dict(hooks._callbacks), list(hooks._pre_routes))
    importlib.reload(th)
    yield
    hooks._single.clear()
    hooks._single.update(saved[0])
    hooks._callbacks.clear()
    hooks._callbacks.update(saved[1])
    hooks._pre_routes[:] = saved[2]


async def _incoming(repo, emp, text, mid):
    await repo.add_message(employee_id=emp.id, staff_id=None, chat_id=emp.chat_id, direction="in", text=text,
                           mid=mid)


async def test_create_notifies_group_with_app_button(session, world, bot):
    repo = SqlRepo(session)
    await repo.set_setting("hr_group_chat_id", world.group_chat)
    await _incoming(repo, world.emp, "Где получить пропуск?", "in-7")
    t = await service.create_ticket(None, repo, world.emp, "Где получить пропуск?", source="unknown")
    assert t.assignee_id == world.rec.id and t.status == "new" and t.category == "unknown"
    note = bot.sent[-1]
    assert note["chat_id"] == world.group_chat
    assert f"Новое обращение #{t.id}: Бот не нашёл ответ" in note["text"]
    assert f"Синтетиков Демьян, {world.site.code}, день {service.employee_day(world.emp)}" in note["text"]
    assert "«Где получить пропуск?»" in note["text"] and "Рекрутер: Подборова М. Ждёт ответа." in note["text"]
    assert buttons(note["attachments"]) == ["ticket_" + str(t.id), f"tk:take:{t.id}"]
    assert note["attachments"][0]["payload"]["buttons"][0][0]["web_app"] == "korsik_test_bot"
    assert t.group_mid == "mid-1"
    msg = (await session.scalars(select(Message).where(Message.mid == "in-7"))).one()
    assert msg.ticket_id == t.id


async def test_red_flag_header(session, world, bot):
    repo = SqlRepo(session)
    await repo.set_setting("hr_group_chat_id", world.group_chat)
    await service.create_ticket(None, repo, world.emp, "Болит спина после смены", source="flag",
                                category="здоровье", level="red")
    assert bot.sent[-1]["text"].startswith("🔴 Красный флаг, обращение #")
    assert ": Здоровье" in bot.sent[-1]["text"]


async def test_without_group_goes_to_recruiter(session, world, bot):
    t = await service.create_ticket(None, SqlRepo(session), world.emp, "Вопрос", source="hr_command")
    assert bot.sent[-1]["chat_id"] == world.rec.chat_id and t.group_mid


async def test_group_falls_back_without_open_app(session, world, bot):
    repo = SqlRepo(session)
    await repo.set_setting("hr_group_chat_id", world.group_chat)
    bot.reject_open_app = True
    t = await service.create_ticket(None, repo, world.emp, "Вопрос", source="hr_command")
    assert buttons(bot.sent[-1]["attachments"]) == [f"tk:take:{t.id}"]


async def test_no_bot_still_creates(session, world):
    t = await service.create_ticket(None, SqlRepo(session), world.emp, "Вопрос", source="button")
    assert t.id and t.group_mid is None and t.category == "contact_request"


async def test_reply_window(session, world, bot):
    repo = SqlRepo(session)
    await repo.set_setting("hr_group_chat_id", world.group_chat)
    t = await service.create_ticket(None, repo, world.emp, "Можно отпуск в ноябре?", source="hr_command")
    await service.reply(session, t, world.hr, "Можно, напиши заявление за 2 недели.")
    assert t.status == "in_progress" and t.assignee_id == world.hr.id and t.first_response_at

    ctx = SimpleNamespace(sender=bot)
    await _incoming(repo, world.emp, "А кому отдать заявление?", "in-9")
    assert await service.route_reply(ctx, repo, world.emp, "А кому отдать заявление?", {"mid": "in-9"}) is True
    msg = (await session.scalars(select(Message).where(Message.mid == "in-9"))).one()
    assert msg.ticket_id == t.id
    group = [s for s in bot.sent if s["chat_id"] == world.group_chat]
    assert group[-1]["text"].startswith(f"Новый ответ в обращении #{t.id}")
    assert bot.sent[-1]["text"] == "Передал HR." and bot.sent[-1]["ticket_id"] == t.id

    # второе сообщение — без повторного «Передал HR»
    n = len(bot.sent)
    await _incoming(repo, world.emp, "Спасибо!", "in-10")
    assert await service.route_reply(ctx, repo, world.emp, "Спасибо!", {"mid": "in-10"}) is True
    assert len(bot.sent) == n + 1 and bot.sent[-1]["chat_id"] == world.group_chat

    # окно истекло — текст идёт дальше по маршруту
    w = world.emp.dialog_data[service.WINDOW_KEY]
    world.emp.dialog_data = {**world.emp.dialog_data,
                             service.WINDOW_KEY: {**w, "until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}}
    assert await service.route_reply(ctx, repo, world.emp, "Где столовая?", {"mid": "in-11"}) is False
    assert service.WINDOW_KEY not in world.emp.dialog_data


async def test_close_ends_window(session, world, bot):
    repo = SqlRepo(session)
    t = await service.create_ticket(None, repo, world.emp, "Вопрос", source="hr_command")
    await service.reply(session, t, world.hr, "Ответ")
    await service.close(session, t, world.hr)
    assert t.status == "closed" and t.closed_at
    assert await service.route_reply(SimpleNamespace(sender=bot), repo, world.emp, "ещё", {}) is False


async def test_take_button_in_group(session, world, bot, ticket_hooks):
    repo = SqlRepo(session)
    await repo.set_setting("hr_group_chat_id", world.group_chat)
    t = await service.create_ticket(None, repo, world.emp, "Вопрос", source="hr_command")
    answers = []

    async def answer(callback_id, *, message=None, notification=None, key=None):
        answers.append({"message": message, "notification": notification})

    ctx = SimpleNamespace(sender=SimpleNamespace(answer=answer))
    cb = hooks.Callback(ctx=ctx, repo=repo, callback_id="cb1", payload=f"tk:take:{t.id}", label="Взять себе",
                        orig_text="", mid=t.group_mid, chat_id=world.group_chat, user_id=world.rec.max_user_id,
                        is_staff=True)
    await hooks.callback_handler("tk")(cb, world.rec)
    assert t.status == "in_progress" and t.assignee_id == world.rec.id
    assert "В работе: Подборова М." in answers[-1]["message"]["text"]

    cb2 = hooks.Callback(ctx=ctx, repo=repo, callback_id="cb2", payload=f"tk:take:{t.id}", label=None,
                         orig_text="", mid=None, chat_id=world.group_chat, user_id=world.mgr.max_user_id,
                         is_staff=True)
    await hooks.callback_handler("tk")(cb2, world.mgr)
    assert answers[-1]["notification"] == "Обращения разбирает HR"


async def test_hooks_registered(ticket_hooks):
    assert hooks.single("create_ticket") is service.create_ticket
    assert any(getattr(p, "__name__", "") == "reply_window" for p in hooks.pre_routes())


async def test_ticket_row(session, world):
    t = await service.create_ticket(None, SqlRepo(session), world.emp, "x", source="survey")
    assert (await session.get(Ticket, t.id)).category == "contact_request"


async def test_category_mapping():
    assert service.normalize_category("flag", "угрозы") == "harassment"
    assert service.normalize_category("flag", "охрана труда") == "safety"
    assert service.normalize_category("survey", "доступы") == "доступы"
    assert service.normalize_category("survey", "contact_request") == "contact_request"
    assert service.normalize_category("unknown", None) == "unknown"
    t = SimpleNamespace(category="доступы", source="survey", is_personal=False)
    assert service.category_label(t) == "Доступы" and service.is_red(t) is False


async def test_reply_window_closed_by_time_machine(session, world, bot):
    """Окно ответа HR — в настоящем времени: после перемотки вопрос не уходит в старое обращение."""
    repo = SqlRepo(session)
    t = await service.create_ticket(None, repo, world.emp, "Как оформить пропуск?", source="hr_command")
    await service.reply(session, t, world.hr, "Пропуск оформляет охрана.")
    assert service.open_window(world.emp)
    world.emp.time_shift_hours = 14 * 24  # ведущий перемотал на две недели
    assert service.open_window(world.emp) is None
    ctx = SimpleNamespace(sender=bot)
    assert await service.route_reply(ctx, repo, world.emp, "Во сколько обед?", {"mid": "in-20"}) is False
