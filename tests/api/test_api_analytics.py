"""GET /api/analytics и /api/analytics/risks: доступ по ролям, форма ответа, фильтр по площадке и уровню.

Реальная БД в транзакции, все люди — синтетические.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Flag, Ticket
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio


async def _signals(session, world):
    now = datetime.now(UTC)
    session.add(Flag(employee_id=world.emp.id, level="red", topic="безопасность", quote="(синтетика)",
                     source="survey", status="new", created_at=now - timedelta(days=1)))
    session.add(Ticket(employee_id=world.other.id, source="unknown", category="unknown", text="Где столовая?",
                       status="new", created_at=now - timedelta(days=2)))
    await session.flush()


async def test_hr_and_head_see_analytics_manager_does_not(client, world, session):
    await _signals(session, world)
    for person in (world.hr, world.rec, world.head):
        r = await client.get(f"/api/analytics?site={world.site.code}", headers=auth(person))
        assert r.status_code == 200, person.role
        r = await client.get(f"/api/analytics/risks?site={world.site.code}", headers=auth(person))
        assert r.status_code == 200, person.role
    for path in ("/api/analytics", "/api/analytics/risks"):
        r = await client.get(path, headers=auth(world.mgr))
        assert r.status_code == 403
        assert r.json()["detail"] == "Аналитика доступна HR и руководителю HR"
    assert (await client.get("/api/analytics")).status_code == 401  # без initData


async def test_summary_shape(client, world, session):
    await _signals(session, world)
    r = await client.get(f"/api/analytics?site={world.site.code}", headers=auth(world.head))
    data = r.json()
    assert data["site"] == world.site.code and data["threshold"] == 5
    assert [f["stage"] for f in data["funnel"]] == ["pre", "week1", "month1", "month2", "month3", "done"]
    assert sum(f["count"] for f in data["funnel"]) == 2
    risks = data["risks"]
    assert risks["total"] == 2 and risks["high"] >= 1 and risks["high"] + risks["medium"] == 2
    assert {x["code"] for x in risks["reasons"]} == {"red_flag", "not_connected", "ticket_waiting"}
    assert data["kpi"]["risks"] == 2 and data["kpi"]["risks_high"] == risks["high"]
    assert set(data["mood"]) == {"overall", "sites", "low_data", "weeks"}
    assert isinstance(data["clusters"], list)
    assert any(s["code"] == world.site.code for s in data["sites"])


async def test_risk_list_with_reasons_and_card_link(client, world, session):
    await _signals(session, world)
    r = await client.get(f"/api/analytics/risks?site={world.site.code}", headers=auth(world.hr))
    items = {i["employee_id"]: i for i in r.json()}
    assert set(items) == {world.emp.id, world.other.id}
    first = items[world.emp.id]
    assert first["level"] == "high" and first["full_name"] == "Синтетиков Демьян"
    assert first["position"] == "Оператор экструдера"
    assert first["reasons"][0]["text"] == "Красный сигнал: безопасность, вчера"
    assert first["card"] == f"/api/employees/{world.emp.id}" and first["start_param"] == f"employee_{world.emp.id}"
    # не подключился к боту (день выхода зависит от выходных) и обращение без ответа HR 2 дня
    other = items[world.other.id]
    assert {x["code"] for x in other["reasons"]} == {"not_connected", "ticket_waiting"}
    assert next(x for x in other["reasons"] if x["code"] == "ticket_waiting")["text"] == "Нет ответа HR 2 дня"
    levels = [i["level"] for i in r.json()]
    assert levels == sorted(levels, key=lambda lv: lv != "high")  # высокий уровень — первым
    only_high = await client.get(f"/api/analytics/risks?site={world.site.code}&level=high", headers=auth(world.hr))
    medium = await client.get(f"/api/analytics/risks?site={world.site.code}&level=medium", headers=auth(world.hr))
    assert world.emp.id in {i["employee_id"] for i in only_high.json()}
    assert len(only_high.json()) + len(medium.json()) == 2
    assert all(i["level"] == "medium" for i in medium.json())
    bad = await client.get("/api/analytics/risks?level=low", headers=auth(world.hr))
    assert bad.status_code == 422
