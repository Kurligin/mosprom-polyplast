"""Права ролей в API Mini App: руководитель видит только своих и не трогает разделы HR,
руководитель HR (head) работает с панелью HR и аналитикой.

Реальная БД в транзакции с откатом (tests/api/conftest.py). Люди синтетические.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.models import ManagerTask
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("method, path, kwargs", [
    ("post", "/api/sites", {"json": {"code": "TSTX"}}),
    ("post", "/api/kb/documents", {"files": {"file": ("memo.pdf", b"%PDF-1.4", "application/pdf")}}),
    ("post", "/api/kb/documents/1/confirm", {}),
    ("post", "/api/kb/answers/1/confirm", {"json": {}}),
    ("post", "/api/scenario/import", {"files": {"file": ("s.xlsx", b"PK", "application/octet-stream")}}),
    ("post", "/api/demo/shift", {"json": {"to": "1"}}),
    ("get", "/api/chats", {}),
])
async def test_manager_cannot_use_hr_sections(client, world, method, path, kwargs):
    r = await getattr(client, method)(path, headers=auth(world.mgr), **kwargs)
    assert r.status_code == 403, (path, r.text)
    assert r.json()["detail"] == "Раздел доступен только HR"


async def test_manager_cannot_open_other_newbie_in_any_way(client, world):
    """Чужой новичок для руководителя не существует: ни карточка, ни фото, ни подмена «глазами руководителя»."""
    h = auth(world.mgr)
    other = world.other.id
    assert (await client.get(f"/api/employees/{other}", headers=h)).status_code == 404
    assert (await client.get(f"/api/employees/{other}?view=manager&manager_id={world.mgr2.id}",
                             headers=h)).status_code == 404
    assert (await client.get(f"/api/employees/{other}/intro-photo", headers=h)).status_code == 404
    rows = (await client.get("/api/employees?mine=0", headers=h)).json()
    assert {e["id"] for e in rows} == {world.emp.id}


async def test_manager_meeting_of_other_newbie_404(client, world, session):
    t = ManagerTask(manager_id=world.mgr2.id, employee_id=world.other.id, day=3, kind="meeting",
                    due_date=world.other.start_date + timedelta(days=2))
    session.add(t)
    await session.flush()
    r = await client.post(f"/api/meetings/{t.id}/done", headers=auth(world.mgr))
    assert r.status_code == 404
    assert (await session.get(ManagerTask, t.id)).status != "done"


async def test_head_marks_any_meeting_and_sees_hr_panel(client, world, session):
    t = ManagerTask(manager_id=world.mgr.id, employee_id=world.emp.id, day=3, kind="meeting",
                    due_date=world.emp.start_date + timedelta(days=2))
    session.add(t)
    await session.flush()
    h = auth(world.head)
    r = await client.post(f"/api/meetings/{t.id}/done", headers=h)
    assert r.status_code == 200, r.text
    assert (await session.get(ManagerTask, t.id)).status == "done"
    assert r.json()["tickets"] is not None and "surveys" in r.json(), "руководитель HR видит карточку как HR"

    rows = {e["id"] for e in (await client.get("/api/employees", headers=h)).json()}
    assert {world.emp.id, world.other.id} <= rows, "руководитель HR видит всех новичков"
    for path in ("/api/tickets", "/api/kb/documents", "/api/topics", "/api/chats", "/api/analytics"):
        assert (await client.get(path, headers=h)).status_code == 200, path


async def test_hr_mine_filter(client, world):
    """«Мои» у HR — новички, где он руководитель: у HR таких нет."""
    rows = (await client.get("/api/employees?mine=1", headers=auth(world.hr))).json()
    assert world.emp.id not in {e["id"] for e in rows} and world.other.id not in {e["id"] for e in rows}
