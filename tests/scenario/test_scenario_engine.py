"""Движок сценария на тестовой БД и фейковом API: склейка, идемпотентность, устаревание, ветки,
отмашки, опросы, машина времени."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.bot.handlers import BotCtx
from app.bot.hooks import Callback
from app.bot.repo import SqlRepo
from app.db.models import Delivery, Employee, ScenarioStep
from app.scenario import engine, store
from app.scenario import texts as T
from app.scenario.models import Button, StepSpec, TimingRule
from tests.scenario.conftest import make_employee

pytestmark = pytest.mark.asyncio
MSK = ZoneInfo("Europe/Moscow")


def S(n, rule, text=None, **kw):
    return StepSpec(number=n, timing=TimingRule(**rule), text=text or f"шаг {n}", **kw)


# Выход в пн 2 ноября 2026: 1-й рабочий день — 2.11, 2-й — 3.11, 4.11 — праздник, 3-й — 5.11
SPECS = [
    S(1, {"kind": "before_start", "days_before": 3}, "Как мы будем общаться?", code="1.1"),
    S(2, {"kind": "before_start", "days_before": 3}, "Список документов", site_tags=["GTP"],
      attachment="img_03.png"),
    S(3, {"kind": "before_start", "days_before": 3}, "Факт №1"),
    S(4, {"kind": "before_start", "days_before": 3}, "До встречи, Х! 👋🏻"),
    S(5, {"kind": "before_start", "days_before": 1}, "Привет, Х! Ты готов?",
      buttons=[Button(label="Да, жду не дождусь!", reply="Супер!")]),
    S(6, {"kind": "workday", "n": 1, "time": "10:00"}, "Поздравляю с первым рабочим днем, Х!",
      attachment="img_04.png"),
    S(7, {"kind": "workday", "n": 1, "time": "10:00"}, "Здравствуйте, Имя кандидата!", channel="email"),
    S(8, {"kind": "workday", "n": 1, "time": "12:30"}, "Хочешь, подскажу, где пообедать?",
      buttons=[Button(label="Да!", action="branch", next_step=9),
               Button(label="Нет, спасибо!", reply="Набери /Food")]),
    S(9, {"kind": "event", "event": "answer", "after_step": 8, "answer": "Да"}, "Приятного аппетита!",
      site_tags=["GTP"], attachment="food_GTP.pdf"),
    S(10, {"kind": "workday", "n": 1, "time": "11:00"}, "Карта офиса", site_tags=["Luzhniki"]),
    S(11, {"kind": "workday", "n": 2, "time": "10:00"}, "Расскажи о себе и приложи фото", gate="recruiter_go"),
    S(12, {"kind": "workday", "n": 3, "time": "17:30"}, "Предлагаем пройти опрос"),
    S(13, {"kind": "workday", "n": 3, "time": "17:30"}, "Вопрос 1", kind="survey"),
    S(14, {"kind": "workday", "n": 3, "time": "17:30"}, "Вопрос 2", kind="survey"),
    S(15, {"kind": "workday", "n": 3, "time": "17:30"}, "Пока, Х!"),
]


def at(day, hour, minute=0, month=11):
    return datetime(2026, month, day, hour, minute, tzinfo=MSK)


class FakeSurveys:
    """Модуль опросов: все шаги-опросы — один опрос day3."""

    def __init__(self):
        self.started: list[str] = []
        self.done = False

    def survey_for_step(self, n):
        return "day3" if int(n) in (13, 14) else None

    async def start_survey(self, repo, emp, code):
        self.started.append(code)
        return "mid-survey"

    def survey_history(self, emp):
        return {"day3": {"started_at": "x", **({"done_at": "y"} if self.done else {})}}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Без чужих модулей: опросы — по тесту, задачи руководителя — заглушка."""
    monkeypatch.setattr(engine, "_surveys", lambda: None)

    async def no_manager(session):
        return None

    monkeypatch.setattr(engine, "_manager_tick", no_manager)
    engine._locks.clear()


@pytest_asyncio.fixture
async def emp(db):
    async with db() as s:
        await store.import_specs(s, SPECS)
        e = make_employee()
        s.add(e)
        await s.commit()
        return e


async def tick(db, sender, now):
    return await engine.tick(now, sender=sender, session_factory=db)


async def deliveries(db, emp_id) -> dict[int, Delivery]:
    async with db() as s:
        rows = (await s.execute(select(ScenarioStep.order_no, Delivery)
                                .join(Delivery, Delivery.step_id == ScenarioStep.id)
                                .where(Delivery.employee_id == emp_id))).all()
        return {n: d for n, d in rows if ":b" not in d.idempotency_key}


async def test_preboarding_merges_text_and_covers_onboarding(db, emp, api, sender):
    assert await tick(db, sender, at(30, 12, month=10)) == 2
    # шаг с картинкой — отдельно, текстовые шаги одного момента — одним сообщением
    assert api.sent[0]["text"] == "Список документов" and api.media(0) == ["image"]
    assert api.sent[1]["text"] == "Факт №1\n\nДо встречи, Тест! 👋🏻" and api.sent[1]["attachments"] == []
    d = await deliveries(db, emp.id)
    assert d[1].status == "sent" and d[1].error == engine.COVERED_NOTE and d[1].mid is None
    assert d[2].mid == "mid1" and d[3].mid == d[4].mid == "mid2"
    # идемпотентность: второй тик ничего не шлёт
    assert await tick(db, sender, at(30, 12, 5, month=10)) == 0
    assert len(api.sent) == 2


async def test_stale_steps_and_email_are_not_sent(db, emp, api, sender):
    assert await tick(db, sender, at(2, 19)) == 2
    assert api.sent[0]["text"] == "Поздравляю с первым рабочим днем, Тест!" and api.media(0) == ["image"]
    assert api.sent[1]["text"] == "Хочешь, подскажу, где пообедать?"
    d = await deliveries(db, emp.id)
    assert {n: d[n].status for n in (2, 3, 4, 5)} == dict.fromkeys((2, 3, 4, 5), "skipped")
    assert d[5].error == engine.STALE_NOTE
    assert d[7].status == "skipped" and d[7].error == engine.EMAIL_NOTE
    assert 10 not in d  # чужая площадка


async def test_button_reply_edits_message_and_branch_sends_file(db, emp, api, sender):
    await tick(db, sender, at(2, 13))
    did = (await deliveries(db, emp.id))[8].id
    assert api.payloads() == [f"sc:{did}:0", f"sc:{did}:1"]
    n_sent = len(api.sent)
    ctx = BotCtx(sender=sender)

    async with db() as s:
        e = await s.get(Employee, emp.id)
        cb = Callback(ctx=ctx, repo=SqlRepo(s), callback_id="cb1", payload=f"sc:{did}:1", label="Нет, спасибо!",
                      orig_text="Хочешь, подскажу, где пообедать?", mid="mid2", chat_id=500, user_id=100)
        await engine.on_button(cb, e)
        await s.commit()
    msg = api.answers[-1]["message"]
    assert msg["text"] == "Хочешь, подскажу, где пообедать?\n\n✅ Нет, спасибо!\n\nНабери /Food"
    assert msg["attachments"] == []  # кнопки убраны
    assert len(api.sent) == n_sent  # ответ — правкой того же сообщения

    async with db() as s:
        e = await s.get(Employee, emp.id)
        cb = Callback(ctx=ctx, repo=SqlRepo(s), callback_id="cb2", payload=f"sc:{did}:0", label="Да!",
                      orig_text="", mid="mid2", chat_id=500, user_id=100)
        await engine.on_button(cb, e)
        await s.commit()
        assert e.dialog_data["sc_answers"]
    assert api.answers[-1]["message"]["text"].endswith("✅ Да!")
    assert api.sent[-1]["text"] == "Приятного аппетита!" and api.media() == ["file"]
    assert ("file", "food_GTP.pdf") in api.uploads


async def test_food_command_sends_site_file(db, emp, api, sender):
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.send_food(BotCtx(sender=sender), SqlRepo(s), e)
        await s.commit()
    assert api.sent[-1]["text"] == f"{T.FOOD_INTRO[0]}\n\nПриятного аппетита!"
    assert api.media() == ["file"]


async def test_gate_recruiter_go_and_intro(db, emp, api, sender):
    await tick(db, sender, at(3, 11))
    assert all("Расскажи о себе" not in m["text"] for m in api.sent)  # ждёт отмашку
    async with db() as s:
        e = await s.get(Employee, emp.id)
        assert await engine.open_gate(s, e, "recruiter_go") == {"recruiter_go": True, "plan_ready": False}
        await s.commit()
    assert await tick(db, sender, at(3, 11, 1)) == 1
    assert api.sent[-1]["text"] == "Расскажи о себе и приложи фото"

    ctx = BotCtx(sender=sender)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        assert e.dialog_state == engine.INTRO_STATE
        await engine.on_intro(ctx, SqlRepo(s), e, "Я инженер-технолог", {"attachments": []})
        assert api.sent[-1]["text"] == T.INTRO_NEED_PHOTO[0]
        photo = {"attachments": [{"type": "image", "payload": {"url": "https://example.test/p.jpg"}}]}
        await engine.on_intro(ctx, SqlRepo(s), e, "", photo)
        await s.commit()
        assert api.sent[-1]["text"] == T.INTRO_THANKS[0]
        assert e.dialog_state is None
        assert e.dialog_data["intro"]["text"] == "Я инженер-технолог"
        assert e.dialog_data["intro"]["photo_url"] == "https://example.test/p.jpg"


async def test_survey_steps_start_one_survey_and_outro_waits(db, emp, api, sender, monkeypatch):
    sv = FakeSurveys()
    monkeypatch.setattr(engine, "_surveys", lambda: sv)
    # 3-й рабочий день, 17:31: подводка — сообщением, два шага-опроса — один опрос
    await tick(db, sender, at(5, 17, 31))
    assert sv.started == ["day3"]
    assert api.sent[-1]["text"] == "Предлагаем пройти опрос"
    d = await deliveries(db, emp.id)
    assert d[13].status == d[14].status == "sent" and 15 not in d
    # «Пока, Х!» — после того как опрос пройден
    assert await tick(db, sender, at(5, 17, 40)) == 0
    sv.done = True
    assert await tick(db, sender, at(5, 17, 41)) == 1
    assert api.sent[-1]["text"] == "Пока, Тест!"


async def test_survey_steps_wait_without_module(db, emp, api, sender):
    await tick(db, sender, at(5, 17, 31))
    d = await deliveries(db, emp.id)
    assert d[12].status == "sent" and 13 not in d and 14 not in d


async def test_time_travel_sends_new_day_without_backlog(db, emp, api, sender):
    real = at(23, 12, month=10)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        res = await engine.time_travel(s, e, "1", now=real)
        await s.commit()
        assert res["day"] == 1 and res["employee_id"] == e.id
        assert engine.adaptation_day(e, real) == 1
        # «сейчас» — сразу после последнего шага дня (12:30) и не раньше 10:00
        assert e.time_shift_hours == 241
    await tick(db, sender, real)
    assert [m["text"] for m in api.sent] == ["Поздравляю с первым рабочим днем, Тест!",
                                            "Хочешь, подскажу, где пообедать?"]
    d = await deliveries(db, emp.id)
    assert d[2].error == d[5].error == engine.TRAVEL_NOTE  # пропущенное не вываливаем
    assert d[1].error == engine.COVERED_NOTE

    # «3» → 3-й рабочий день (4.11 — праздник, значит 5.11, день 4)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        res = await engine.time_travel(s, e, "3", now=real)
        await s.commit()
        assert res["day"] == 4
    await tick(db, sender, real)
    assert api.sent[-1]["text"] == "Предлагаем пройти опрос"
    d = await deliveries(db, emp.id)
    assert 11 not in d  # шаг по отмашке не пропускаем: отмашка может прийти позже

    # тестового новичка можно вернуть назад и показать заново
    async with db() as s:
        e = await s.get(Employee, emp.id)
        res = await engine.time_travel(s, e, "-2", now=real)
        await s.commit()
        assert res["day"] == -2
        upcoming = await engine.upcoming_steps(s, e, now=real)
        assert upcoming[0]["at"].startswith("2026-11-01T10:00")  # «за день до выхода»
    n = len(api.sent)
    await tick(db, sender, real)
    assert [m["text"] for m in api.sent[n:]] == ["Список документов", "Факт №1\n\nДо встречи, Тест! 👋🏻"]

    async with db() as s:
        e = await s.get(Employee, emp.id)
        res = await engine.time_travel(s, e, "now", now=real)
        assert e.time_shift_hours == 0 and res["day"] == -9
        up = await engine.upcoming_steps(s, e, now=real)
        assert up and up[0]["at"].startswith("2026-11-01T10:00")


async def test_on_onboarded_sends_due_steps_in_handler(db, emp, api, sender, monkeypatch):
    monkeypatch.setattr(engine, "utcnow", lambda: at(30, 12, month=10))
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.on_onboarded(BotCtx(sender=sender), SqlRepo(s), e)
        await s.commit()
    assert [m["text"] for m in api.sent] == ["Список документов", "Факт №1\n\nДо встречи, Тест! 👋🏻"]


async def test_adaptation_and_probation_days():
    e = SimpleNamespace(start_date=datetime(2026, 11, 1).date(), probation_end=datetime(2027, 1, 31).date(),
                        time_shift_hours=0)
    # 1.11 — воскресенье: день выхода — понедельник 2.11
    assert engine.adaptation_day(e, at(2, 9)) == 1
    assert engine.adaptation_day(e, at(1, 9)) == 0
    e.time_shift_hours = 48
    assert engine.adaptation_day(e, at(2, 9)) == 3
    assert engine.probation_days(e) == 91


async def test_replay_closes_gates_of_replayed_days(db, emp, api, sender):
    real = at(23, 12, month=10)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.open_gate(s, e, "recruiter_go")
        e.dialog_state = engine.INTRO_STATE
        await engine.time_travel(s, e, "3", now=real)
        assert engine.gates(e)["recruiter_go"] is True  # шаг 2-го дня раньше цели — отмашка остаётся
        await engine.time_travel(s, e, "1", now=real)
        await s.commit()
        assert engine.gates(e) == {"recruiter_go": False, "plan_ready": False}
        assert e.dialog_state is None
