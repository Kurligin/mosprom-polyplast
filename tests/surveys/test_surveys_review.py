"""Опросы: случаи из ревью. Двойной тап по варианту не пишет ответ, флаг и обращение дважды; напоминания
не держат блокировки новичков до конца задачи.

`world` — фейковый «МАКС» и БД в откатываемой транзакции (fakes.py). Для напоминаний — отдельная тестовая БД
с настоящими коммитами: блокировки видны только между транзакциями. Люди синтетические.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.bot import runtime
from app.bot.handlers import handle_update
from app.config import settings
from app.db.base import Base
from app.db.models import Employee, Flag, Site, SurveyAnswer, Ticket
from app.surveys import engine
from app.surveys import texts as T
from tests.surveys.fakes import ev_press

pytestmark = pytest.mark.asyncio


async def _count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


# --- 14. двойной тап по варианту ------------------------------------------------------------------------

async def test_double_tap_leave_option_records_once(world):
    w = world
    mid = await engine.start_survey(w.repo, w.emp, "final")
    await w.press("Со всем справился", mid)
    second = ev_press(w.api, mid, "Нет, задумываюсь об уходе")  # второй тап, пока первый ещё в работе
    await w.press("Нет, задумываюсь об уходе", mid)
    view = w.api.text(mid)
    await handle_update(w.ctx, w.repo, second)
    await w.session.flush()
    eid = w.emp.id
    assert await _count(w.session, SurveyAnswer, SurveyAnswer.employee_id == eid,
                        SurveyAnswer.value == "Нет, задумываюсь об уходе") == 1
    assert await _count(w.session, Flag, Flag.employee_id == eid, Flag.level == "red") == 1
    assert await _count(w.session, Ticket, Ticket.employee_id == eid) == 1
    assert w.api.text(mid) == view, "вопрос «что повлияло?» на месте"
    assert w.api.toasts[-1] == T.ANSWER_SAVED


async def test_double_tap_low_score_records_once(world):
    w = world
    mid = await engine.start_survey(w.repo, w.emp, "week2")
    await w.press("Да", mid)
    await w.press("Да", mid)
    second = ev_press(w.api, mid, "5")
    await w.press("5", mid)
    await handle_update(w.ctx, w.repo, second)
    await w.session.flush()
    assert await _count(w.session, SurveyAnswer, SurveyAnswer.employee_id == w.emp.id,
                        SurveyAnswer.score.is_not(None)) == 1, "вторая оценка не сдвигает средние"


# --- 20. напоминания не держат блокировки ------------------------------------------------------------------

TEST_DB = "korsik_test_surveys_jobs"


@pytest_asyncio.fixture
async def db():
    url = make_url(settings.database_url)
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            if not await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}):
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres недоступен: {e}")
    finally:
        await admin.dispose()
    eng = create_async_engine(url.set(database=TEST_DB), poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    await eng.dispose()


def _waiting(n: int) -> Employee:
    st = {"code": "day3", "q": 0, "mid": None, "started_at": "2026-10-20T14:30:00+00:00", "reminded": 0,
          "done": False, "sel": [], "answer_id": None, "wait": None}
    return Employee(full_name=f"Ждущий {n} (синтетика)", first_name=f"Ждущий{n}", site_code="GrPP",
                    work_type="office", start_date=date(2026, 10, 15), status="active", max_user_id=1000 + n,
                    chat_id=2000 + n, address_form="ty", dialog_data={"onboarded": True, "survey": st})


async def test_reminders_release_lock_after_each_newcomer(db, monkeypatch):
    async with db() as s:
        s.add(Site(code="GrPP", name="GrPP (синтетика)", tz="Europe/Moscow"))
        await s.flush()
        first, second = _waiting(1), _waiting(2)
        s.add_all([first, second])
        await s.commit()
    locked: list[bool] = []

    async def send(repo, text_, *, employee=None, **kw):
        if employee.id == second.id:  # первый новичок уже получил напоминание и жмёт «Продолжить опрос»
            async with db() as bot:
                try:
                    await bot.execute(select(Employee.id).where(Employee.id == first.id)
                                      .with_for_update(key_share=True, nowait=True))
                    locked.append(False)
                except DBAPIError:
                    locked.append(True)
        return ["mid"]

    monkeypatch.setattr(runtime, "send", send)
    day1 = datetime.fromisoformat("2026-10-21T08:00:00+00:00")  # среда, 11:00 МСК
    async with db() as s:
        assert await engine.remind_all(s, day1) == 2
        await s.commit()
    assert locked == [False], "бот не ждёт, пока задача разошлёт напоминания всем"
    async with db() as s:
        for e in (first, second):
            assert (await s.get(Employee, e.id)).dialog_data["survey"]["reminded"] == 1, "напоминание записано"

