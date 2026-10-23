"""Сценарий: случаи из ревью. Перемотка забывает вопрос о публикации рассказа; фоновая отправка шагов после
перемотки ждёт коммита API; детектор вопросов в ожидании рассказа о себе.

Тестовая БД и фейковый API «МАКС» (conftest.py). Люди синтетические.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.bot import runtime
from app.bot.repo import lock_employee
from app.db.models import Employee
from app.scenario import engine, store
from app.scenario.models import StepSpec, TimingRule
from tests.scenario.conftest import make_employee

pytestmark = pytest.mark.asyncio
MSK = ZoneInfo("Europe/Moscow")

SPECS = [
    StepSpec(number=1, timing=TimingRule(kind="workday", n=1, time="10:00"), text="Поздравляю с первым днём, Х!"),
    StepSpec(number=2, timing=TimingRule(kind="workday", n=2, time="10:00"), text="Расскажи о себе и приложи фото",
             gate="recruiter_go"),
    StepSpec(number=3, timing=TimingRule(kind="workday", n=3, time="10:00"), text="Третий день"),
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(engine, "_surveys", lambda: None)

    async def no_manager(session):
        return None

    monkeypatch.setattr(engine, "_manager_tick", no_manager)


@pytest_asyncio.fixture
async def emp(db):
    async with db() as s:
        await store.import_specs(s, SPECS)
        e = make_employee()
        s.add(e)
        await s.commit()
        return e


# --- 3. перемотка забывает и вопрос «опубликовать рассказ?» ------------------------------------------------

async def test_rewind_forgets_intro_post_with_intro(db, emp):
    real = datetime(2026, 10, 23, 12, 0, tzinfo=MSK)
    async with db() as s:
        e = await s.get(Employee, emp.id)
        await engine.open_gate(s, e, "recruiter_go")
        engine._set_data(e, intro={"text": "Я Тест, работаю на экструдере пять лет", "photo_url": "p", "at": "x"},
                         intro_post={"status": "asked", "chat_id": -2, "mid": "mid9", "intro_at": "x"})
        await engine.time_travel(s, e, "1", now=real)
        await s.commit()
        assert e.dialog_data.get("intro") is None
        assert e.dialog_data.get("intro_post") is None, "старый вопрос о публикации забыт вместе с рассказом"


# --- 16. детектор вопроса в ожидании рассказа ---------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Как получить пропуск на территорию завода",
    "Скажите, пожалуйста, где тут столовая",
    "Какой график работы у бухгалтерии в пятницу",
    "Кто мой наставник на линии экструзии",
    "Есть ли у нас корпоративное обучение по охране труда",
    "Что делать, если не выдали спецодежду на смену",
    "Как мне оформить пропуск на парковку для машины",
])
async def test_question_is_not_intro(text):
    assert not engine._is_intro_text(text)


@pytest.mark.parametrize("text", [
    "Как оператор экструдера, я пять лет работал на заводе упаковки",
    "Что обо мне: люблю походы, шахматы и хорошие книги",
    "Я Анна, пять лет работала технологом на заводе упаковки",
])
async def test_story_is_intro(text):
    assert engine._is_intro_text(text)


# --- 19. отправка шагов после перемотки ждёт коммита API -------------------------------------------------

async def test_deliver_soon_waits_for_api_commit(db, emp, sender, monkeypatch):
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "async_session", db)
    seen: list[int] = []

    async def deliver_due(session, e, **kw):
        seen.append(e.time_shift_hours)
        return 0

    monkeypatch.setattr(engine, "deliver_due", deliver_due)
    runtime.set_ctx(SimpleNamespace(sender=sender))
    try:
        async with db() as api_session:  # API: перемотка под блокировкой новичка, коммит — позже
            e = await api_session.get(Employee, emp.id)
            await lock_employee(api_session, e)
            e.time_shift_hours = 48
            await api_session.flush()
            engine._deliver_soon(e.id, delay=0)
            await asyncio.sleep(0.3)  # фоновая отправка стартовала раньше коммита API
            await api_session.commit()
        await asyncio.wait_for(asyncio.gather(*list(engine._background)), 5)
    finally:
        runtime.set_ctx(None)
    assert seen == [48], "шаги считаются по новому «сейчас» новичка, а не по старому"
