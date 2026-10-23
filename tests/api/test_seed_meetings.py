"""Встречи с руководителем у синтетических новичков: прошедшие проведены, кроме 1–2 для демо «пора встретиться».

Реальная БД в транзакции. Все люди — синтетические.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Employee, ManagerTask
from seed.seed import add_months, settle_meetings

pytestmark = pytest.mark.asyncio


async def _synth(session, world, name: str, days_ago: int) -> Employee:
    start = date.today() - timedelta(days=days_ago)
    e = Employee(full_name=f"{name} [синтетика]", first_name=name, site_code=world.site.code, work_type="office",
                 start_date=start, probation_end=add_months(start, 3), manager_id=world.mgr.id,
                 recruiter_id=world.rec.id, status="active", is_synthetic=True)
    session.add(e)
    await session.flush()
    return e


async def test_past_meetings_done_except_kept(session, world):
    old = await _synth(session, world, "Давний", 45)      # прошли встречи 3-го и 30-го дня
    mid = await _synth(session, world, "Средний", 12)     # прошла встреча 3-го дня
    fresh = await _synth(session, world, "Свежий", 1)     # встреч ещё не было
    today = date.today()
    done, kept = await settle_meetings(session, [old, mid, fresh], today, keep_overdue=1)

    tasks = list(await session.scalars(select(ManagerTask).where(
        ManagerTask.employee_id.in_([old.id, mid.id, fresh.id]))))
    past = [t for t in tasks if t.due_date < today]
    assert len(past) >= 3
    pending = [t for t in past if t.status == "pending"]
    assert len(pending) == 1 and kept == 1 and done == len(past) - 1
    latest = max(past, key=lambda t: t.due_date)
    assert pending[0].due_date == latest.due_date  # «пора встретиться» — по самой свежей встрече
    for t in past:
        if t.status == "done":
            assert t.done_at is not None and t.done_at.date() == t.due_date
    assert all(t.status == "pending" for t in tasks if t.due_date >= today)

    # повторный запуск ничего не ломает: оставленная встреча так и ждёт
    done2, kept2 = await settle_meetings(session, [old, mid, fresh], today, keep_overdue=1)
    assert (done2, kept2) == (0, 1)


async def test_settle_skips_not_synthetic(session, world):
    real = await _synth(session, world, "Настоящий", 45)
    real.is_synthetic = False
    await session.flush()
    done, kept = await settle_meetings(session, [real], date.today())
    assert (done, kept) == (0, 0)
