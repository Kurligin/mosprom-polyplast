"""Аналитика: случаи из ревью. Кластеры тем не раскрывают автора (порог 5, тема одного человека, площадки,
цитаты с фамилиями и по флагам), обращение «в работе» без ответа HR, оценка по недоотвеченному опросу.

Чистые функции — без БД, остальное — реальная БД в транзакции с откатом (conftest.py). Люди синтетические.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.analytics import clusters, risks, summary
from app.analytics.clusters import aggregate, has_name, pick_quotes
from app.db.models import Consent, SurveyAnswer, Ticket
from app.surveys import store

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
LO = NOW - timedelta(weeks=4)


def row(eid, topic="доступы", *, text=None, site="GTP", mood="negative", days=1, rid=None):
    return {"id": rid or eid * 100 + days, "employee_id": eid, "site": site, "topic": topic, "mood": mood,
            "score": None, "text": text, "evidence_quote": None, "created_at": NOW - timedelta(days=days),
            "synthetic": True}


def people(n: int, *, site="GTP", start=1, text="Учётки нет третий день, сижу без дела", **kw) -> list[dict]:
    return [row(start + i, site=site, text=text, **kw) for i in range(n)]


# --- 8. кластеры не раскрывают автора ---------------------------------------------------------------------

def test_topic_of_one_person_is_not_a_cluster():
    out = aggregate([row(1, "обучение", text="План адаптации под другую должность", days=1),
                     row(1, "обучение", days=2)], LO, consents={1})
    assert out == [], "тема одного человека — его история, а не тема"


def test_quotes_only_from_five_people():
    few = aggregate(people(4), LO, consents={1, 2, 3, 4})
    assert few[0]["people"] == 4 and few[0]["quotes"] == [] and few[0]["quotes_reason"] == "few"
    many = aggregate(people(5), LO, consents={1, 2, 3, 4, 5})
    assert len(many[0]["quotes"]) == 2 and many[0]["quotes_reason"] is None


def test_sites_below_threshold_are_merged():
    rows = people(5, site="GTP") + people(1, site="Luzhniki", start=10)
    (c,) = aggregate(rows, LO)
    assert [(s["code"], s["count"], s["people"]) for s in c["sites"]] == [("GTP", 5, 5), ("other", 1, 1)]
    assert c["sites"][1]["name"] == "Другие площадки"
    (c,) = aggregate(people(3, site="GTP") + people(2, site="GrPP", start=10), LO)
    assert c["sites"] == [], "ни одна площадка не набрала 5 человек: разбивки нет"


def test_quotes_have_no_site():
    (c,) = aggregate(people(5), LO, consents={1, 2, 3, 4, 5})
    assert c["quotes"] and all(set(q) == {"text", "mood", "at"} for q in c["quotes"])


def test_flagged_answers_are_not_quoted():
    rows = people(5)
    rows[0]["text"] = "Мастер заставляет работать без защитных очков"
    rows[0]["created_at"] = NOW  # самая свежая: без исключения попала бы первой
    (c,) = aggregate(rows, LO, consents={1, 2, 3, 4, 5}, flagged={rows[0]["id"]})
    assert all("очков" not in q["text"] for q in c["quotes"]), "этот текст уже в именном обращении"


# --- 9. цитаты без имён ------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Мастер Сидоров орёт на всех, Петрович тоже",
    "Наставник Саша Кузнецов не отвечает",
    "петрович опять кричит на планёрке",
    "Иванов И. И. ничего не объясняет",
    "Сидоров. Петрович. Оба не помогают",
])
def test_names_left_after_mask(text):
    assert has_name(text)


@pytest.mark.parametrize("text", [
    "Обещали обучение на экструдере, а три месяца я на подхвате. Узнаю всё от коллег",
    "Учётку в 1С обещали завести к обеду. Пока работаю через коллегу",
    "[ФИО] обещал учётку, звоню ему [ТЕЛЕФОН], пока тишина.",
    "Офис в Лужниках далеко от метро, ИТ отвечает через неделю",
    "Обратная связь только в виде крика на планерке. При всех. Прям очень мотивирует 👍",
])
def test_plain_complaints_pass(text):
    assert not has_name(text, {"Лужники"})


def test_pick_quotes_drops_unmasked_names():
    rows = [row(1, text="Мастер Сидоров орёт на всех, Петрович тоже"),
            row(2, text="Наставник Саша Кузнецов не отвечает"),
            row(3, text="Учётки нет третий день, сижу без дела")]
    assert [q["text"] for q in pick_quotes(rows, {1, 2, 3}, [])] == ["Учётки нет третий день, сижу без дела"]


# --- 13. почему нет цитат ---------------------------------------------------------------------------------

def test_quotes_reason_no_text_and_hidden():
    (buttons,) = aggregate([row(i) for i in range(1, 6)], LO, consents={1, 2, 3, 4, 5})
    assert buttons["quotes"] == [] and buttons["quotes_reason"] == "no_text"
    (hidden,) = aggregate(people(5), LO, consents=set())
    assert hidden["quotes"] == [] and hidden["quotes_reason"] == "hidden", "не раскрываем, что согласия нет"


# --- 8 на БД: мало ответивших на площадке — кластеров нет --------------------------------------------------------

async def _answer(s, ids, emp, code, key, *, at, **kw):
    a = SurveyAnswer(employee_id=emp.id, question_id=ids[code][key], is_synthetic=True, created_at=at, **kw)
    s.add(a)
    await s.flush()
    return a


@pytest.mark.asyncio
async def test_site_with_few_respondents_has_no_clusters(world):
    s = world.session
    ids = await store.sync(s)
    now = datetime.now(UTC)
    emps = [await world.new(f"Ответил Номер{i}") for i in range(5)]
    for e in emps[:4]:
        s.add(Consent(employee_id=e.id, purpose="ai_analysis", given=True, given_at=now - timedelta(days=30)))
        await _answer(s, ids, e, "week2", "w2_info", value="Нет", topic="доступы", mood="negative",
                      text="Нет доступа к почте вторую неделю", at=now - timedelta(days=2))
    assert await clusters(s, 4, now, site=world.site.code) == [], "4 ответивших: темы не показываем"
    data = await summary(s, now, site=world.site.code)
    assert data["clusters"] == [] and data["clusters_low_data"] is True
    await _answer(s, ids, emps[4], "day1", "d1_access", value="Да, все есть", mood="positive",
                  at=now - timedelta(days=3))
    (c,) = await clusters(s, 4, now, site=world.site.code)
    assert (c["topic"], c["people"], c["quotes"], c["quotes_reason"]) == ("доступы", 4, [], "few")


# --- 10. обращение «в работе» без ответа HR ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_taken_ticket_without_answer_is_waiting(world):
    s = world.session
    now = datetime.now(UTC)
    e = await world.new("Взяли Вработу", days_ago=12)
    s.add(Ticket(employee_id=e.id, source="unknown", category="unknown", text="Когда дадут доступ к 1С?",
                 status="in_progress", assignee_id=world.rec.id, created_at=now - timedelta(days=3, hours=1)))
    await s.flush()
    got = {i.employee_id: i for i in await risks(s, now, site=world.site.code)}
    assert [r.text for r in got[e.id].reasons] == ["Нет ответа HR 3 дня"]


# --- 12. оценка по недоотвеченному опросу ---------------------------------------------------------------------

W2 = ("w2_manager", "w2_feedback", "w2_colleagues", "w2_workplace", "w2_access", "w2_hr", "w2_hr_hiring")


@pytest.mark.asyncio
async def test_partial_final_survey_is_not_a_score_drop(world):
    s = world.session
    ids = await store.sync(s)
    now = datetime.now(UTC)
    e = await world.new("Отвлёкся Посреди", days_ago=88)
    for i, key in enumerate(W2):
        await _answer(s, ids, e, "week2", key, score=9, value="9", at=now - timedelta(days=74, minutes=i))
    await _answer(s, ids, e, "final", "fin_load", score=4, value="4", at=now - timedelta(minutes=1))
    got = {i.employee_id: i for i in await risks(s, now, site=world.site.code)}
    assert e.id not in got, "одна шкала из восьми — ещё не оценка опроса"
    e.dialog_data = {"surveys": {"final": {"started_at": now.isoformat(), "done_at": now.isoformat()}}}
    await s.flush()
    got = {i.employee_id: i for i in await risks(s, now, site=world.site.code)}
    assert [r.text for r in got[e.id].reasons] == ["Оценка упала с 9 до 4"], "опрос пройден: средняя считается"
