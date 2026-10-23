"""Риски, кластеры и воронка на реальной БД в транзакции. Свои новички на своей площадке, все — синтетические."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.analytics import clusters, funnel, risks, summary
from app.db.models import Consent, Flag, ManagerTask, SurveyAnswer, Ticket
from app.privacy import names
from app.surveys import store

pytestmark = pytest.mark.asyncio


async def answer(session, ids, emp, code, key, *, at, score=None, value=None, text=None, mood=None, topic=None):
    a = SurveyAnswer(employee_id=emp.id, question_id=ids[code][key], score=score, value=value, text=text,
                     mood=mood, topic=topic, is_synthetic=True, created_at=at)
    session.add(a)
    await session.flush()
    return a


def by_id(items) -> dict:
    return {i.employee_id: i for i in items}


async def test_risks_rules_on_db(world):
    s = world.session
    ids = await store.sync(s)
    now = datetime.now(UTC)
    red = await world.new("Красный Сигнал", days_ago=20)
    s.add(Flag(employee_id=red.id, level="red", topic="травля", quote="(синтетика)", source="survey", status="new",
               created_at=now - timedelta(days=2)))

    drop = await world.new("Падение Оценок", days_ago=88)
    for i, key in enumerate(("w2_manager", "w2_feedback", "w2_colleagues")):
        await answer(s, ids, drop, "week2", key, score=9, value="9", at=now - timedelta(days=74, minutes=i))
    for i, key in enumerate(("fin_load", "fin_manager", "fin_goals")):
        await answer(s, ids, drop, "final", key, score=5, value="5", at=now - timedelta(hours=2, minutes=i))
    await answer(s, ids, drop, "final", "fin_stay", value="Не уверен", mood="neutral", at=now - timedelta(hours=2))

    waiting = await world.new("Без Бота", days_ago=3, status="waiting", synthetic=False)
    meeting = await world.new("Без Встречи", days_ago=40)
    s.add(ManagerTask(manager_id=world.mgr.id, employee_id=meeting.id, day=30, kind="meeting", status="pending",
                      due_date=date.today() - timedelta(days=10)))
    ticket = await world.new("Без Ответа", days_ago=12)
    s.add(Ticket(employee_id=ticket.id, source="unknown", category="unknown", text="Где взять пропуск?",
                 status="new", created_at=now - timedelta(days=2, hours=1)))
    stopped = await world.new("Отключил Бота", days_ago=9, status="stopped")
    calm = await world.new("Всё Хорошо", days_ago=15)
    gone = await world.new("Уже Ушёл", days_ago=30, status="finished")
    over = await world.new("Испытательный Прошёл", days_ago=100, probation=90)
    for e in (gone, over):
        s.add(Flag(employee_id=e.id, level="red", topic="угрозы", quote="(синтетика)", source="survey",
                   status="new", created_at=now - timedelta(days=1)))
    await s.flush()

    items = await risks(s, now, site=world.site.code)
    got = by_id(items)
    assert calm.id not in got and gone.id not in got and over.id not in got  # не в адаптации или всё хорошо
    assert [r.code for r in got[red.id].reasons] == ["red_flag"] and got[red.id].level == "high"
    assert [r.text for r in got[drop.id].reasons] == ["Оценка упала с 9 до 5",
                                                       "Сомневается, что останется после испытательного"]
    assert got[drop.id].level == "high" and got[drop.id].stage == "month3"
    assert got[waiting.id].reasons[0].code == "not_connected" and got[waiting.id].stage == "week1"
    assert got[meeting.id].reasons[0].text.startswith("Встреча на 30-й день: просрочена на ")
    assert got[ticket.id].reasons[0].text == "Нет ответа HR 2 дня"
    assert [r.code for r in got[stopped.id].reasons] == ["bot_stopped"]
    # сначала высокий уровень
    levels = [i.level for i in items]
    assert levels == sorted(levels, key=lambda lv: lv != "high")
    d = got[red.id].to_dict()
    assert d["card"] == f"/api/employees/{red.id}" and d["start_param"] == f"employee_{red.id}"
    assert d["full_name"] == "Красный Сигнал" and d["reasons"][0]["label"] == "красные сигналы"


async def test_silent_and_missed_from_survey_history(world):
    """Ответил в первый день, опрос 3-го дня закрыт следующим, чек 2-й недели висит: молчит и 2 пропуска подряд."""
    s = world.session
    ids = await store.sync(s)
    now = datetime.now(UTC)
    e = await world.new("Тихий Новичок", days_ago=20)
    await answer(s, ids, e, "day1", "d1_access", value="Да, все есть", mood="positive",
                 at=now - timedelta(days=20))
    day3, week2 = (now - timedelta(days=17)).isoformat(), (now - timedelta(days=6)).isoformat()
    e.dialog_data = {
        "surveys": {"day3": {"started_at": day3, "closed_at": week2}, "week2": {"started_at": week2}},
        "survey": {"code": "week2", "q": 1, "mid": None, "started_at": week2, "reminded": 2, "done": False,
                   "sel": [], "answer_id": None, "wait": None},
    }
    await s.flush()
    got = by_id(await risks(s, now, site=world.site.code))
    texts = [r.text for r in got[e.id].reasons]
    assert "2 опроса подряд без ответа" in texts
    assert "Молчит 20 дней, нет ответа на «Чек через 2 недели»" in texts


async def test_clusters_quotes_need_ai_consent_and_are_masked(world):
    s = world.session
    names.invalidate()
    ids = await store.sync(s)
    now = datetime.now(UTC)
    agree, silent, revoked = (await world.new("Согласен Первый"), await world.new("Без Согласия"),
                              await world.new("Передумал Третий"))
    s.add(Consent(employee_id=agree.id, purpose="ai_analysis", given=True, given_at=now - timedelta(days=30)))
    s.add(Consent(employee_id=revoked.id, purpose="ai_analysis", given=True, given_at=now - timedelta(days=30)))
    s.add(Consent(employee_id=revoked.id, purpose="ai_analysis", given=False, given_at=now - timedelta(days=5)))
    # текущие 4 недели: доступы — 3 упоминания (2 текста и негативная кнопка), позитивные — не упоминания
    await answer(s, ids, agree, "week2", "w2_info", value="Нет", topic="доступы", mood="negative",
                 text="Начальников Пётр Андреевич обещал учётку, звоню ему 8 916 123-45-67, пока тишина.",
                 at=now - timedelta(days=3))
    await answer(s, ids, silent, "week2", "w2_info", value="Нет", topic="доступы", mood="negative",
                 text="Нет доступа к почте вторую неделю", at=now - timedelta(days=2))
    await answer(s, ids, revoked, "day1", "d1_access", value="Нет", mood="negative", at=now - timedelta(days=4))
    await answer(s, ids, revoked, "week2", "w2_tasks", value="Нет", topic="понимание задач", mood="negative",
                 text="Не понимаю, что от меня хотят", at=now - timedelta(days=4))
    await answer(s, ids, agree, "day3", "d3_colleagues", value="Отлично!", mood="positive",
                 at=now - timedelta(days=10))
    await answer(s, ids, agree, "week2", "w2_tasks", value="Да", topic="понимание задач", mood="positive",
                 text="Всё понятно, спасибо", at=now - timedelta(days=3))
    await answer(s, ids, agree, "final", "fin_improve", topic="другое", mood="negative", text="Всё плохо",
                 at=now - timedelta(days=1))
    await answer(s, ids, agree, "plan", "m1_plan", value="Я отстаю от графика.", topic="нагрузка",
                 mood="negative", text="Болит спина после смены, не успеваю", at=now - timedelta(days=1))
    # прошлые 4 недели: доступы — 1
    await answer(s, ids, silent, "day1", "d1_access", value="Нет", mood="negative", at=now - timedelta(days=40))

    cl = {c["topic"]: c for c in await clusters(s, 4, now, site=world.site.code)}
    assert "другое" not in cl and "коллектив" not in cl  # «другое» и позитив — не кластер
    acc = cl["доступы"]
    assert (acc["count"], acc["prev"], acc["people"], acc["trend"]) == (3, 1, 3, "up")
    assert acc["trend_text"] == "в 3 раза больше, чем месяц назад"
    assert acc["sites"] == [{"code": world.site.code, "count": 3, "prev": 1, "people": 3}]
    assert [q["text"] for q in acc["quotes"]] == ["[ФИО] обещал учётку, звоню ему [ТЕЛЕФОН], пока тишина."]
    # нет согласия на ИИ-разбор (или его отозвали) — нет цитаты; тема здоровья — тоже без цитаты
    assert cl["понимание задач"]["quotes"] == [] and cl["понимание задач"]["count"] == 1
    assert cl["нагрузка"]["quotes"] == [] and cl["нагрузка"]["trend_text"] == "новая тема"


async def test_funnel_counts_stages(world):
    s = world.session
    now = datetime.now(UTC)
    await world.new("До Выхода", days_ago=-5, status="waiting")
    await world.new("Первая Неделя", days_ago=1)
    await world.new("Первый Месяц", days_ago=20)
    await world.new("Второй Месяц", days_ago=45)
    await world.new("Третий Месяц", days_ago=75)
    await world.new("Недавно Завершил", days_ago=100, probation=90)
    await world.new("Давно Завершил", days_ago=300, probation=90)
    rows = await funnel(s, now, site=world.site.code)
    assert [r["stage"] for r in rows] == ["pre", "week1", "month1", "month2", "month3", "done"]
    assert [r["count"] for r in rows] == [1, 1, 1, 1, 1, 1]
    assert rows[0]["label"] == "До выхода" and rows[-1]["label"] == "Завершили"


async def test_summary_shape_and_small_site_threshold(world):
    s = world.session
    now = datetime.now(UTC)
    e = await world.new("Один Новичок", days_ago=12)
    s.add(Flag(employee_id=e.id, level="yellow", topic="нагрузка", quote="(синтетика)", source="survey",
               status="new", created_at=now - timedelta(days=1)))
    await s.flush()
    data = await summary(s, now, site=world.site.code)
    assert set(data) >= {"funnel", "mood", "clusters", "risks", "kpi", "window", "threshold"}
    assert data["risks"]["total"] == 1 and data["risks"]["medium"] == 1
    assert data["risks"]["reasons"] == [{"code": "yellow_flag", "label": "жёлтые сигналы", "count": 1}]
    assert next(r for r in data["funnel"] if r["stage"] == "month1")["risk"] == 1
    assert len(data["mood"]["weeks"]) == 8 and data["mood"]["weeks"][-1]["current"] is True
    # меньше 5 ответивших — ни среза, ни цифр
    assert all(not w["shown"] and w["avg_score"] is None for w in data["mood"]["weeks"])
    assert data["mood"]["sites"] == []

