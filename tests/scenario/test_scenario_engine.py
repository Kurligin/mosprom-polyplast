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
    about = "Я инженер-технолог, пять лет на экструзии"
    async with db() as s:
        e = await s.get(Employee, emp.id)
        assert e.dialog_state is None and engine.intro_wait(e), "ждём рассказ в dialog_data, а не в dialog_state"
        assert await engine.on_intro(ctx, SqlRepo(s), e, about, {"attachments": []}) is True
        assert api.sent[-1]["text"] == T.INTRO_NEED_PHOTO[0]
        photo = {"attachments": [{"type": "image", "payload": {"url": "https://example.test/p.jpg"}}]}
        assert await engine.on_intro(ctx, SqlRepo(s), e, "", photo) is True
        await s.commit()
        assert api.sent[-1]["text"] == T.INTRO_THANKS[0]
        assert e.dialog_state is None and engine.intro_wait(e) is None
        assert e.dialog_data["intro"]["text"] == about
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
        # отправки «будущих» дней из репетиции забыты: шаги за 3 дня до выхода снова впереди
        assert up and up[0]["at"].startswith("2026-10-30T10:00")


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
        e.dialog_data = {**e.dialog_data, engine.INTRO_WAIT: {"since": engine._vnow(e).isoformat(), "asked": []}}
        await engine.time_travel(s, e, "3", now=real)
        assert engine.gates(e)["recruiter_go"] is True  # шаг 2-го дня раньше цели — отмашка остаётся
        await engine.time_travel(s, e, "1", now=real)
        await s.commit()
        assert engine.gates(e) == {"recruiter_go": False, "plan_ready": False}
        assert e.dialog_state is None and engine.INTRO_WAIT not in e.dialog_data


async def test_timeline_past_from_deliveries_and_upcoming(db, emp, api, sender):
    await tick(db, sender, at(2, 19))
    async with db() as s:
        e = await s.get(Employee, emp.id)
        tl = await engine.timeline(s, e, now=at(2, 19))
    # знакомство в боте (шаг 1) — пройдено; устаревшие и письмо — нет
    assert [p["at"][:16] for p in tl["past"]] == ["2026-10-30T10:00", "2026-11-02T10:00", "2026-11-02T12:30"]
    assert [u["at"][:16] for u in tl["upcoming"]] == ["2026-11-03T10:00", "2026-11-05T17:30"]
    assert all(p["title"] for p in tl["past"] + tl["upcoming"])
    assert tl["now"].startswith("2026-11-02T19:00")
    async with db() as s:
        e = await s.get(Employee, emp.id)
        assert await engine.upcoming_steps(s, e, limit=1, now=at(2, 19)) == tl["upcoming"][:1]


async def test_timeline_synthetic_past_from_plan(db):
    async with db() as s:
        await store.import_specs(s, SPECS)
        e = make_employee(full_name="Синтетиков Демьян [синтетика]", is_synthetic=True, is_test=False,
                          max_user_id=None, chat_id=None, dialog_data={})
        s.add(e)
        await s.commit()
        tl = await engine.timeline(s, e, now=at(3, 12))
    # отправок нет: прошедшие — шаги плана до «сейчас», без письма и без шага по отмашке (3.11 10:00)
    assert [p["at"][:16] for p in tl["past"]] == ["2026-10-30T10:00", "2026-11-01T10:00", "2026-11-02T10:00",
                                                  "2026-11-02T12:30"]
    assert [u["at"][:16] for u in tl["upcoming"]] == ["2026-11-05T17:30"]
    # приглашён, но не подключился: пройденных шагов нет
    async with db() as s:
        e = await s.get(Employee, e.id)
        e.status = "waiting"
        tl = await engine.timeline(s, e, now=at(3, 12))
    assert tl["past"] == []


class DoneSurveys(FakeSurveys):
    """Опрос day3 с временем прохождения и сбросом (машина времени)."""

    def __init__(self):
        super().__init__()
        self.done_at: str | None = None
        self.resets: list[str] = []

    def survey_history(self, emp):
        return {"day3": {"started_at": "x", **({"done_at": self.done_at} if self.done_at else {})}}

    def reset(self, emp, code=None):
        self.resets.append(code)


async def test_outro_after_survey_survives_next_day(db, emp, api, sender, monkeypatch):
    """Реплика после опроса не устаревает, пока опрос не пройден: пройден на следующий день — приходит."""
    sv = DoneSurveys()
    monkeypatch.setattr(engine, "_surveys", lambda: sv)
    await tick(db, sender, at(5, 17, 31))
    assert sv.started == ["day3"]
    await tick(db, sender, at(6, 18, 0))  # больше суток после 17:30, опрос ещё не пройден
    assert 15 not in await deliveries(db, emp.id), "«Пока, Х!» ждёт опрос, а не помечен устаревшим"
    sv.done_at = at(6, 18, 5).isoformat()  # прошёл по напоминанию
    assert await tick(db, sender, at(6, 18, 6)) == 1
    assert api.sent[-1]["text"] == "Пока, Тест!"


async def test_outro_goes_stale_a_day_after_survey(db, emp, api, sender, monkeypatch):
    sv = DoneSurveys()
    monkeypatch.setattr(engine, "_surveys", lambda: sv)
    await tick(db, sender, at(5, 17, 31))
    sv.done_at = at(5, 17, 40).isoformat()
    await tick(db, sender, at(6, 19, 0))  # тик не работал больше суток после прохождения
    d = await deliveries(db, emp.id)
    assert d[15].status == "skipped" and d[15].error == engine.STALE_NOTE


async def test_back_to_now_forgets_rehearsed_days(db, emp, api, sender, monkeypatch):
    """Тестовый новичок: «вернуть к сегодняшнему дню» забывает отправки, опросы и отмашки будущих дней."""
    sv = DoneSurveys()
    monkeypatch.setattr(engine, "_surveys", lambda: sv)
    ticks = []

    async def manager_tick(session):
        ticks.append(1)

    monkeypatch.setattr(engine, "_manager_tick", manager_tick)
    real = at(23, 12, month=10)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.time_travel(s, e, "3", now=real)
        await s.commit()
    await tick(db, sender, real)
    assert sv.started == ["day3"]
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.open_gate(s, e, "recruiter_go")
        e.dialog_data = {**e.dialog_data, "ticket_window": {"ticket_id": 1, "until": real.isoformat(), "shift": 0}}
        await s.commit()

    async with db() as s:
        e = await s.get(Employee, emp.id)
        res = await engine.time_travel(s, e, "now", now=real)
        await s.commit()
        assert res["day"] == -9 and e.time_shift_hours == 0
        assert engine.gates(e) == {"recruiter_go": False, "plan_ready": False}
        assert "ticket_window" not in e.dialog_data
        tl = await engine.timeline(s, e, now=real)
    left = await deliveries(db, emp.id)
    assert all(d.scheduled_at <= real for d in left.values()), "отправок «из будущего» не осталось"
    assert sv.resets == ["day3"] and ticks, "опрос 3-го дня забыт, встречи пересчитаны"
    assert tl["past"] == [] and tl["upcoming"][0]["at"].startswith("2026-10-30T10:00")


async def test_time_travel_end_lands_inside_probation(db, emp, api, sender):
    """Конец срока в субботу: перемотка «end» — на пятницу, push об итоговой встрече ещё уместен."""
    from datetime import date

    from app.manager import service as manager

    real = at(23, 12, month=10)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        e.start_date, e.probation_end = date(2026, 10, 26), date(2027, 1, 23)
        await engine.time_travel(s, e, "end", now=real)
        await s.commit()
        vnow = engine.employee_now(engine.employee_ctx(e, "Europe/Moscow"), real)
        assert vnow.date() == date(2027, 1, 22)
        (final,) = [m for m in manager.plan_meetings(e.start_date, e.probation_end) if m.kind == "probation"]
        task = SimpleNamespace(kind=final.kind, day=final.day, due_date=final.due)
        assert manager.push_is_due(task, e, vnow)


async def test_intro_step_has_skip_button(db, emp, api, sender):
    await tick(db, sender, at(3, 11))
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.open_gate(s, e, "recruiter_go")
        await s.commit()
    assert await tick(db, sender, at(3, 11, 1)) == 1
    did = (await deliveries(db, emp.id))[11].id
    assert api.payloads() == [f"sc:{did}:{engine.INTRO_SKIP}"]
    async with db() as s:
        e = await s.get(Employee, emp.id)
        assert engine.intro_wait(e)
        cb = Callback(ctx=BotCtx(sender=sender), repo=SqlRepo(s), callback_id="cb1",
                      payload=f"sc:{did}:{engine.INTRO_SKIP}", label=T.INTRO_SKIP_BUTTON,
                      orig_text="Расскажи о себе и приложи фото", mid="mid1", chat_id=500, user_id=100)
        await engine.on_button(cb, e)
        await s.commit()
        assert engine.intro_wait(e) is None
    msg = api.answers[-1]["message"]
    assert msg["text"].endswith(T.INTRO_SKIPPED[0]) and msg["attachments"] == []
