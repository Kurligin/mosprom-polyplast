"""Сводка на реальной БД в транзакции: открытые сигналы старше окна, догон пропущенной недели.

Все люди — синтетические.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.db.models import Employee, Flag, Setting, Site
from app.reports import weekly

pytestmark = pytest.mark.asyncio

MON = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)   # понедельник, 09:00 МСК
THU = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


async def _employee(session) -> Employee:
    site = Site(code="TREP1", name="Тестовая площадка сводки (синтетика)", tz="Europe/Moscow")
    session.add(site)
    await session.flush()
    emp = Employee(full_name="Сводкин Пример [синтетика]", first_name="Пример", site_code=site.code,
                   work_type="office", start_date=date.today() - timedelta(days=60),
                   probation_end=date.today() + timedelta(days=30), status="active", is_synthetic=True)
    session.add(emp)
    await session.flush()
    return emp


async def test_open_signals_older_than_window(session):
    """Красный флаг пять недель назад так и не закрыли: в «Открытых сигналах» он остаётся."""
    emp = await _employee(session)
    now = datetime.now(UTC)
    session.add_all([
        Flag(employee_id=emp.id, level="red", topic="намерение уйти", quote="(синтетика)", source="survey",
             status="new", created_at=now - timedelta(days=35)),
        Flag(employee_id=emp.id, level="yellow", topic="нагрузка", quote="(синтетика)", source="survey",
             status="resolved", created_at=now - timedelta(days=35)),
        Flag(employee_id=emp.id, level="yellow", topic="доступы", quote="(синтетика)", source="survey",
             status="resolved", created_at=now - timedelta(days=3)),
    ])
    await session.flush()
    p = weekly.period(now)
    raw = await weekly.collect(session, p)
    mine = sorted((f["status"], f["level"]) for f in raw.flags if f["employee_id"] == emp.id)
    assert mine == [("new", "red"), ("resolved", "yellow")]  # старый закрытый — вне окна


async def test_send_due_catches_up_once(session, monkeypatch):
    sent = []

    async def fake_send(s, now=None, *, week_end=None):
        sent.append(week_end)
        return True

    monkeypatch.setattr(weekly, "send", fake_send)
    await session.execute(delete(Setting).where(Setting.key == weekly.SENT_KEY))
    assert await weekly.send_due(session, THU)  # в понедельник приложение не работало: догоняем в четверг
    assert not await weekly.send_due(session, THU + timedelta(hours=1))
    assert sent == [date(2026, 10, 18)]
    assert (await session.get(Setting, weekly.SENT_KEY)).value == "2026-10-18"
    assert await weekly.send_due(session, MON + timedelta(days=7))
    assert sent[-1] == date(2026, 10, 25)


async def test_send_due_retries_when_not_sent(session, monkeypatch):
    calls = []

    async def not_sent(s, now=None, *, week_end=None):
        calls.append(week_end)
        return False  # руководитель HR ещё не подключился или бот выключен

    monkeypatch.setattr(weekly, "send", not_sent)
    await session.execute(delete(Setting).where(Setting.key == weekly.SENT_KEY))
    assert not await weekly.send_due(session, MON)
    assert not await weekly.send_due(session, MON + timedelta(hours=1))
    assert calls == [date(2026, 10, 18)] * 2
    assert await session.get(Setting, weekly.SENT_KEY) is None
