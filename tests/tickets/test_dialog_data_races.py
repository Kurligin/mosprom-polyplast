"""Гонки записи dialog_data: бот, планировщик и API пишут JSONB целиком, поздний коммит не должен затирать чужое.

Нужны настоящие коммиты из двух сессий, поэтому БД — отдельная тестовая (tests/scenario/conftest.py: db).
Все люди — синтетические.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.bot import runtime
from app.bot.repo import SqlRepo
from app.db.models import Employee, Staff, Ticket
from app.surveys import config as C
from app.surveys import engine as surveys
from app.surveys import store
from app.tickets import service as tickets
from tests.scenario.conftest import make_employee

pytestmark = pytest.mark.asyncio


class Sender:
    """Отправка в «МАКС»; пока она идёт, «другой процесс» может закоммитить своё (during)."""

    def __init__(self, during=None):
        self.during = during
        self.sent: list[str] = []

    async def send(self, repo, text, **kw):
        if self.during is not None:
            await self.during()
            self.during = None
        self.sent.append(text)
        return [f"mid-{len(self.sent)}"]

    async def edit(self, mid, text, *, attachments=None, key=None):
        return True


@pytest.fixture(autouse=True)
def _no_bot():
    yield
    runtime.set_ctx(None)


async def _employee(db, **data) -> int:
    async with db() as s:
        e = make_employee(is_test=False, dialog_data={"onboarded": True, **data})
        s.add(e)
        await s.commit()
        return e.id


async def _patch_data(db, emp_id: int, **data) -> None:
    """Другой процесс (бот, задача) меняет свой ключ dialog_data и коммитит."""
    async with db() as s:
        e = await s.get(Employee, emp_id)
        e.dialog_data = {**(e.dialog_data or {}), **data}
        await s.commit()


async def _data(db, emp_id: int) -> dict:
    async with db() as s:
        return dict((await s.get(Employee, emp_id)).dialog_data or {})


async def test_hr_reply_keeps_survey_written_by_bot(db):
    emp_id = await _employee(db)
    async with db() as s:
        hr = Staff(full_name="Ответова Ольга (HR, синтетика)", role="hr")
        s.add(hr)
        await s.flush()
        t = Ticket(employee_id=emp_id, source="hr_command", text="Можно отпуск?", status="new")
        s.add(t)
        await s.commit()
        ids = (hr.id, t.id)

    # пока ответ HR уходит в «МАКС», новичок отвечает на опрос, и бот коммитит новое состояние
    sender = Sender(lambda: _patch_data(db, emp_id, survey={"code": "day3", "q": 1}))
    runtime.set_ctx(SimpleNamespace(sender=sender))
    async with db() as s:
        held = await s.get(Employee, emp_id)  # API уже загрузил новичка
        await tickets.reply(s, await s.get(Ticket, ids[1]), await s.get(Staff, ids[0]), "Можно, пиши заявление")
        await s.commit()
        assert held.dialog_data["survey"] == {"code": "day3", "q": 1}
    data = await _data(db, emp_id)
    assert data["survey"] == {"code": "day3", "q": 1}, "ответ HR не откатил состояние опроса"
    assert data[tickets.WINDOW_KEY]["ticket_id"] == ids[1]


async def test_survey_start_keeps_window_written_by_api(db, monkeypatch):
    C.load()
    async with db() as s:
        monkeypatch.setitem(store._ids, "map", await store.sync(s))
        monkeypatch.setitem(store._ids, "mtime", C._cache["mtime"])
        await s.commit()
    emp_id = await _employee(db)
    runtime.set_ctx(SimpleNamespace(sender=Sender()))
    window = {"ticket_id": 7, "until": datetime.now(UTC).isoformat(), "relayed": False, "shift": 0}
    async with db() as s:
        emp = await s.get(Employee, emp_id)  # тик загрузил новичка
        await _patch_data(db, emp_id, ticket_window=window)  # HR ответил из Mini App
        assert await surveys.start_survey(SqlRepo(s), emp, "day3")
        await s.commit()
    data = await _data(db, emp_id)
    assert data["survey"]["code"] == "day3"
    assert data[tickets.WINDOW_KEY] == window, "запуск опроса не затёр окно ответа HR"


async def test_reminders_keep_window_written_meanwhile(db):
    st = {"code": "day3", "q": 0, "mid": "m1", "started_at": "2026-10-20T14:30:00+00:00", "reminded": 0,
          "done": False, "sel": [], "answer_id": None, "wait": None}
    emp_id = await _employee(db, survey=st)
    runtime.set_ctx(SimpleNamespace(sender=Sender()))
    window = {"ticket_id": 9, "until": datetime.now(UTC).isoformat(), "relayed": False, "shift": 0}
    async with db() as s:
        held = await s.get(Employee, emp_id)  # сессия задачи уже держит новичка в памяти
        await _patch_data(db, emp_id, ticket_window=window)  # HR ответил из Mini App
        assert await surveys.remind_all(s, datetime(2026, 10, 21, 8, 0, tzinfo=UTC), [emp_id]) == 1
        await s.commit()
        assert held.dialog_data[tickets.WINDOW_KEY] == window
    data = await _data(db, emp_id)
    assert data["survey"]["reminded"] == 1
    assert data[tickets.WINDOW_KEY] == window, "напоминание не затёрло окно ответа HR"


async def test_bot_event_locks_employee_row(db):
    """Событие бота держит строку новичка до коммита: планировщик и API подождут и перечитают свежие данные."""
    emp_id = await _employee(db)
    async with db() as bot:
        assert (await SqlRepo(bot).employee_by_user(100)).id == emp_id
        async with db() as other:
            with pytest.raises(DBAPIError):
                await other.execute(select(Employee).where(Employee.id == emp_id)
                                    .with_for_update(key_share=True, nowait=True))
            await other.rollback()
        await bot.commit()
    async with db() as other:  # после коммита события строка свободна
        await other.execute(text("SELECT id FROM employees WHERE id = :i FOR NO KEY UPDATE NOWAIT"), {"i": emp_id})
