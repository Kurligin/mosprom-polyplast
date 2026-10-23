"""Машина времени — только для тестовых и синтетических новичков; DEMO_ENABLED=false выключает демо целиком.

Все люди — синтетические; «живой» новичок здесь — синтетика без пометок is_test / is_synthetic.
"""

from datetime import date, timedelta

import pytest

from app.config import settings
from app.db.models import Employee
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio


@pytest.fixture
def travels(monkeypatch):
    """Движок сценария не трогаем: запоминаем, кого перематывали."""
    from app.scenario import engine

    calls = []

    async def fake(session, emp, to):
        calls.append((emp.id, to))
        return {"employee_id": emp.id, "day": 3, "message": "день 3"}

    monkeypatch.setattr(engine, "time_travel", fake)
    return calls


async def _live_employee(session, world) -> Employee:
    start = date.today() - timedelta(days=4)
    emp = Employee(full_name="Живой Пилотов [синтетика теста]", first_name="Пилот", site_code=world.site.code,
                   work_type="production", start_date=start, probation_end=start + timedelta(days=90),
                   manager_id=world.mgr.id, recruiter_id=world.rec.id, max_user_id=world.hr.max_user_id + 100,
                   chat_id=world.hr.max_user_id + 101, status="active", is_test=False, is_synthetic=False)
    session.add(emp)
    await session.flush()
    return emp


async def test_shift_real_employee_forbidden(client, world, session, travels):
    emp = await _live_employee(session, world)
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "60", "employee_id": emp.id})
    assert r.status_code == 403, r.text
    assert travels == []
    await session.refresh(emp)
    assert emp.time_shift_hours == 0


async def test_shift_test_and_synthetic_allowed(client, world, session, travels):
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "3", "employee_id": world.emp.id})
    assert r.status_code == 200, r.text  # синтетический
    emp = await _live_employee(session, world)
    emp.is_test = True
    await session.flush()
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "3", "employee_id": emp.id})
    assert r.status_code == 200, r.text
    assert travels == [(world.emp.id, "3"), (emp.id, "3")]


async def test_demo_disabled(client, world, monkeypatch, travels):
    monkeypatch.setattr(settings, "demo_enabled", False)
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "3", "employee_id": world.emp.id})
    assert r.status_code == 404
    r = await client.post("/api/demo/report", headers=auth(world.hr), json={})
    assert r.status_code == 404
    assert travels == []


async def test_demo_disabled_still_requires_login(client, world, monkeypatch):
    monkeypatch.setattr(settings, "demo_enabled", False)
    r = await client.post("/api/demo/shift", json={"to": "3"})
    assert r.status_code == 401
