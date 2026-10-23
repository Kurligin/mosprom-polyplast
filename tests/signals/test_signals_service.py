"""check_text / raise_flag / employee_notice / ИИ-разбор / красный флаг в свободном сообщении."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import handle_update
from app.db.models import Consent, Flag, SurveyAnswer, SurveyQuestion, Ticket
from app.signals import hooks as signal_hooks
from app.signals import service
from tests.surveys.fakes import ev_text

pytestmark = pytest.mark.asyncio

DAY = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)     # 12:00 МСК
NIGHT = datetime(2026, 10, 23, 21, 0, tzinfo=UTC)  # 00:00 МСК


async def _answer(world, text: str) -> SurveyAnswer:
    q = await world.session.scalar(select(SurveyQuestion).limit(1))
    ans = SurveyAnswer(employee_id=world.emp.id, question_id=q.id, text=text)
    world.session.add(ans)
    await world.session.flush()
    return ans


async def _flags(world) -> list[Flag]:
    return (await world.session.scalars(select(Flag).where(Flag.employee_id == world.emp.id))).all()


async def test_red_flag_ticket_and_group(world):
    ans = await _answer(world, "Мастер угрожает уволить, если пожалуюсь")
    flags = await service.check_text(world.repo, world.emp, ans.text, source="survey", answer=ans)
    assert [(f.level, f.topic, f.source, f.answer_id) for f in flags] == [("red", "угрозы", "survey", ans.id)]
    t = await world.session.get(Ticket, flags[0].ticket_id)
    assert (t.source, t.category, t.assignee_id) == ("flag", "harassment", world.rec.id)
    assert t.text == ans.text
    group = [world.api.text(m) for m in world.api.sent(900)]
    assert group and group[-1].startswith("🔴 Красный флаг") and "Мастер угрожает" in group[-1]

    again = await service.check_text(world.repo, world.emp, ans.text, source="survey", answer=ans)
    assert [f.id for f in again] == [flags[0].id], "тот же ответ не плодит флаги"


async def test_yellow_flag_no_ticket(world):
    flags = await service.check_text(world.repo, world.emp, "Ищу другую работу, тут скучно", source="survey")
    assert [(f.level, f.topic, f.ticket_id) for f in flags] == [("yellow", "намерение уйти", None)]
    assert await service.employee_notice(world.repo, world.emp, flags, DAY) is None


async def test_notice_text_day_night_emergency(world):
    bully = await service.check_text(world.repo, world.emp, "Коллеги унижают при всех", source="survey")
    day = await service.employee_notice(world.repo, world.emp, bully, DAY)
    assert day == "Это важно. Я сразу передал твоё сообщение HR, его увидит только HR."
    night = await service.employee_notice(world.repo, world.emp, bully, NIGHT)
    assert night.endswith("Если нужна срочная помощь, звони 112.")
    threat = await service.check_text(world.repo, world.emp, "Бригадир сказал, что прибьёт", source="survey")
    assert "112" in await service.employee_notice(world.repo, world.emp, threat, DAY)
    world.emp.address_form = "vy"
    assert "ваше сообщение" in await service.employee_notice(world.repo, world.emp, bully, DAY)
    for text in (day, night):
        assert not any(ch in text for ch in "🐾😞👍🙂—"), "серьёзно, без эмодзи и длинных тире"


async def test_ai_only_with_consent_and_not_health(world, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "schedule_analysis", lambda aid, text, names, eid: calls.append(aid))
    ans = await _answer(world, "Не хватает наставника, всё узнаю сам")
    await service.check_text(world.repo, world.emp, ans.text, source="survey", answer=ans)
    assert calls == [], "без согласия ai_analysis в LLM не отправляем"

    world.session.add(Consent(employee_id=world.emp.id, purpose="ai_analysis", given=True))
    await world.session.flush()
    await service.check_text(world.repo, world.emp, ans.text, source="survey", answer=ans)
    assert calls == [ans.id]

    sick = await _answer(world, "Был на больничном, давление скачет")
    await service.check_text(world.repo, world.emp, sick.text, source="survey", answer=sick)
    assert calls == [ans.id], "здоровье в LLM не уходит"

    world.session.add(Consent(employee_id=world.emp.id, purpose="ai_analysis", given=False))
    await world.session.flush()
    await service.check_text(world.repo, world.emp, ans.text, source="survey", answer=ans)
    assert calls == [ans.id], "отозвал согласие — не отправляем"


async def test_analyze_answer_later_writes_topic_mood_quote(world, monkeypatch):
    from app.db import session as db_session
    from app.llm import gigachat

    async def fake_analyze(text, names=None):
        assert "Опросова" not in text or names, "имена передаются для маскирования"
        return {"topic": "охрана труда", "mood": "негатив", "quote": "дали на два размера больше"}

    @asynccontextmanager
    async def same_tx():
        s = AsyncSession(bind=world.session.bind, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield s
        finally:
            await s.close()

    monkeypatch.setattr(gigachat, "analyze_answer", fake_analyze)
    monkeypatch.setattr(db_session, "async_session", same_tx)
    ans = await _answer(world, "Спецовку дали на два размера больше")
    await world.session.commit()  # savepoint: фоновая сессия должна увидеть ответ
    res = await service.analyze_answer_later(ans.id, ans.text, ["Опросова Анна"], world.emp.id)
    assert res["topic"] == "охрана труда"
    await world.session.refresh(ans)
    assert (ans.topic, ans.mood, ans.evidence_quote) == ("охрана труда", "negative", "дали на два размера больше")
    llm_flags = [f for f in await _flags(world) if f.source == "llm"]
    assert [(f.level, f.topic) for f in llm_flags] == [("yellow", "охрана труда")]


async def test_free_text_red_flag_pre_route(world, monkeypatch):
    from app.bot import hooks

    monkeypatch.setattr(hooks, "_pre_routes", [signal_hooks.red_flag_in_message])
    await handle_update(world.ctx, world.repo, ev_text("Меня мастер толкнул и обещал побить"))
    flags = await _flags(world)
    assert [(f.level, f.topic, f.source) for f in flags] == [("red", "угрозы", "message")]
    reply = world.api.text(world.api.last(500))
    assert reply.startswith("Это важно.") and "112" in reply

    before = len(world.api.sent(500))
    await handle_update(world.ctx, world.repo, ev_text("Что делать при травме на производстве?"))
    assert len(await _flags(world)) == 1, "общий вопрос о травме идёт в базу знаний, не в флаги"
    assert len(world.api.sent(500)) == before + 1


async def test_repeated_red_flag_goes_to_open_ticket(world, monkeypatch):
    """Второе сообщение об угрозе за 12 часов: то же обращение, сообщение в его ленте, HR узнаёт."""
    from app.bot import hooks
    from app.db.models import Message

    monkeypatch.setattr(hooks, "_pre_routes", [signal_hooks.red_flag_in_message])
    await handle_update(world.ctx, world.repo, ev_text("Мастер угрожает мне, говорит что побьёт после смены"))
    (flag,) = await _flags(world)
    group_before = len(world.api.sent(900))

    again = "Он опять угрожал мне сегодня, мне страшно идти на смену"
    await handle_update(world.ctx, world.repo, ev_text(again))
    await world.session.flush()
    assert [f.id for f in await _flags(world)] == [flag.id], "флаг той же темы не дублируется"
    tickets = (await world.session.scalars(select(Ticket).where(Ticket.employee_id == world.emp.id))).all()
    assert [t.id for t in tickets] == [flag.ticket_id], "новое обращение не нужно"
    msg = (await world.session.scalars(select(Message).where(Message.text == again,
                                                             Message.direction == "in"))).one()
    assert msg.ticket_id == flag.ticket_id, "второе сообщение — в ленте обращения"
    group = [world.api.text(m) for m in world.api.sent(900)[group_before:]]
    assert len(group) == 1 and group[0].startswith(f"Новый ответ в обращении #{flag.ticket_id}") and again in group[0]
    assert world.api.text(world.api.last(500)).startswith("Это важно.")

    # обращение закрыли — следующая тревога открывает новое
    t = tickets[0]
    t.status = "closed"
    await world.session.flush()
    await handle_update(world.ctx, world.repo, ev_text("Мастер снова угрожает мне, что побьёт"))
    tickets = (await world.session.scalars(select(Ticket).where(Ticket.employee_id == world.emp.id))).all()
    assert len(tickets) == 2
