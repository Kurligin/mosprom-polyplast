"""Карточка новичка: путь по сценарию, опросы, «подключился», фото представления. Реальная БД в транзакции.

Все люди и ответы — синтетические.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api import views
from app.db.models import Delivery, ScenarioStep, Survey, SurveyAnswer, SurveyQuestion
from app.scenario import engine
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio
MSK = ZoneInfo("Europe/Moscow")


def _at(days: int, hour: int) -> str:
    """Момент по Москве: days — от сегодняшнего дня."""
    d = datetime.now(MSK).date() + timedelta(days=days)
    return datetime(d.year, d.month, d.day, hour, tzinfo=MSK).isoformat()


def _fake_timeline(past, upcoming):
    async def timeline(session, emp, limit=5, *, now=None, plan=None):
        return {"past": [{"at": a, "title": t} for a, t in past],
                "upcoming": [{"at": a, "title": t} for a, t in upcoming][:limit],
                "now": _at(0, 12)}
    return timeline


async def _card(client, world, emp=None):
    r = await client.get(f"/api/employees/{(emp or world.emp).id}", headers=auth(world.hr))
    assert r.status_code == 200, r.text
    return r.json()


# --- шаги ----------------------------------------------------------------------

async def test_steps_done_current_upcoming(client, world, monkeypatch):
    monkeypatch.setattr(engine, "timeline", _fake_timeline(
        past=[(_at(-1, 10), "Поздравление с первым днём"), (_at(0, 10), "Представление коллегам")],
        upcoming=[(_at(0, 17), "Опрос 3-го дня"), (_at(2, 10), "Корпоративные мероприятия"),
                  (_at(2, 11), "Корпоративные мероприятия")]))
    c = await _card(client, world)
    assert [(s["title"], s["status"]) for s in c["steps"]] == [
        ("Поздравление с первым днём", "done"),
        ("Представление коллегам", "current"),  # сегодня, уже отправлен
        ("Опрос 3-го дня", "upcoming"),
        ("Корпоративные мероприятия", "upcoming"),  # одинаковые моменты одного дня — один пункт
    ]
    assert c["current_step"] == "Представление коллегам"
    today = datetime.now(MSK).date().isoformat()
    assert c["steps"][1]["date"] == today and isinstance(c["steps"][1]["day"], int)
    short = next(e for e in (await client.get("/api/employees", headers=auth(world.hr))).json()
                 if e["id"] == world.emp.id)
    assert short["current_step"] == "Представление коллегам"


async def test_current_is_todays_upcoming_or_none(client, world, monkeypatch):
    monkeypatch.setattr(engine, "timeline", _fake_timeline(
        past=[(_at(-1, 10), "Поздравление с первым днём")],
        upcoming=[(_at(0, 17), "Опрос 3-го дня"), (_at(3, 10), "Чек второй недели")]))
    c = await _card(client, world)
    assert [s["status"] for s in c["steps"]] == ["done", "current", "upcoming"]

    monkeypatch.setattr(engine, "timeline", _fake_timeline(
        past=[(_at(-1, 10), "Поздравление с первым днём")], upcoming=[(_at(3, 10), "Чек второй недели")]))
    c = await _card(client, world)
    assert [s["status"] for s in c["steps"]] == ["done", "upcoming"]
    assert c["current_step"] == "Чек второй недели"  # сегодня шагов нет — ближайший


async def test_steps_from_real_engine_are_well_formed(client, world):
    """Без подмены: синтетический новичок, шаги из плана сценария в БД (если сценарий загружен)."""
    c = await _card(client, world)
    assert all(set(s) == {"day", "title", "date", "status"} for s in c["steps"])
    order = {"done": 0, "current": 1, "upcoming": 2}
    ranks = [order[s["status"]] for s in c["steps"]]
    assert ranks == sorted(ranks) and ranks.count(1) <= 1
    assert sum(1 for s in c["steps"] if s["status"] != "done") <= 5


# --- опросы ----------------------------------------------------------------------

async def _sync_surveys(session) -> dict[str, Survey]:
    from app.surveys import store

    await store.sync(session)
    return {s.code: s for s in await session.scalars(select(Survey))}


async def test_surveys_from_history_pending_and_message_steps(client, world, session):
    rows = await _sync_surveys(session)
    now = datetime.now(UTC)
    emp = world.emp
    emp.dialog_data = {
        **emp.dialog_data,
        "survey": {"code": "day3", "q": 1, "done": False, "started_at": (now - timedelta(hours=3)).isoformat()},
        "surveys": {
            "day1": {"started_at": (now - timedelta(days=3)).isoformat(),
                     "done_at": (now - timedelta(days=3, hours=-1)).isoformat()},
            "day3": {"started_at": (now - timedelta(hours=3)).isoformat()},
            "week2": {"started_at": (now - timedelta(days=5)).isoformat()},
        },
    }
    # шаг 47 в xlsx — «сообщение», но это опрос «plan»: ищем через survey_for_step, а не по типу шага
    step = ScenarioStep(key=f"test-47-{emp.id}", order_no=47, kind="message", text="синтетика", is_active=False)
    session.add(step)
    await session.flush()
    session.add(Delivery(employee_id=emp.id, step_id=step.id, scheduled_at=now - timedelta(days=1),
                         status="sent", idempotency_key=f"test-47:{emp.id}", sent_at=now - timedelta(days=1)))
    # ответ на вопрос day1 со шкалой нет — оценки нет; ответ на day3 есть, но опрос не закончен
    q3 = await session.scalar(select(SurveyQuestion).where(SurveyQuestion.survey_id == rows["day3"].id).limit(1))
    session.add(SurveyAnswer(employee_id=emp.id, question_id=q3.id, value="Отлично!", is_synthetic=True))
    await session.flush()

    c = await _card(client, world)
    got = {s["id"]: s["status"] for s in c["surveys"]}
    assert got == {rows["day1"].id: "done", rows["day3"].id: "pending", rows["week2"].id: "missed",
                   rows["plan"].id: "pending"}
    assert c["pending_surveys"] == 3
    plan = next(s for s in c["surveys"] if s["id"] == rows["plan"].id)
    assert plan["title"] == rows["plan"].title and plan["score"] is None


async def test_synthetic_without_history_done_by_answers(client, world, session):
    rows = await _sync_surveys(session)
    q = await session.scalar(select(SurveyQuestion).where(SurveyQuestion.survey_id == rows["week2"].id,
                                                          SurveyQuestion.kind == "scale").limit(1))
    session.add_all([SurveyAnswer(employee_id=world.emp.id, question_id=q.id, value="7", score=7, is_synthetic=True),
                     SurveyAnswer(employee_id=world.emp.id, question_id=q.id, value="9", score=9, is_synthetic=True)])
    await session.flush()
    c = await _card(client, world)
    assert [(s["id"], s["status"], s["score"]) for s in c["surveys"]] == [(rows["week2"].id, "done", 8)]
    assert c["pending_surveys"] == 0


# --- подключился -----------------------------------------------------------------

async def test_connected_synthetic_active_only(client, world, session):
    def row(data, emp):
        return next(e for e in data if e["id"] == emp.id)

    data = (await client.get("/api/employees", headers=auth(world.hr))).json()
    assert row(data, world.other)["connected"] is False  # синтетика в статусе waiting
    world.other.status = "active"
    await session.flush()
    data = (await client.get("/api/employees", headers=auth(world.hr))).json()
    assert row(data, world.other)["connected"] is True
    world.emp.is_synthetic = False
    world.emp.max_user_id = None
    await session.flush()
    data = (await client.get("/api/employees", headers=auth(world.hr))).json()
    assert row(data, world.emp)["connected"] is False  # настоящий новичок без привязки


# --- фото представления ----------------------------------------------------------

def _intro(emp, photo):
    emp.dialog_data = {**emp.dialog_data, "intro": {"text": "Всем привет! (синтетика)", "photo_url": photo,
                                                    "at": "2026-10-20T10:00:00+00:00"}}


async def test_intro_photo_external_url_as_is(client, world):
    url = "https://i.oneme.ru/i?r=synthetic-photo"
    _intro(world.emp, url)
    c = await _card(client, world)
    assert c["intro"]["photo_url"] == url
    r = await client.get(f"/api/employees/{world.emp.id}/intro-photo", headers=auth(world.hr))
    assert r.status_code == 307 and r.headers["location"] == url


async def test_intro_photo_file_via_signed_url(client, world, monkeypatch, tmp_path):
    photo = tmp_path / "intro.jpg"
    photo.write_bytes(b"\xff\xd8\xff synthetic jpeg")
    monkeypatch.setattr(views, "PHOTO_ROOTS", (tmp_path,))
    _intro(world.emp, str(photo))
    c = await _card(client, world)
    url = c["intro"]["photo_url"]
    assert url.startswith(f"/api/employees/{world.emp.id}/intro-photo?") and "sig=" in url

    r = await client.get(url)  # <img> без заголовка initData: хватает подписи
    assert r.status_code == 200 and r.content == photo.read_bytes()
    assert r.headers["content-type"] == "image/jpeg"

    assert (await client.get(url.replace("sig=", "sig=0"))).status_code == 403
    expired = f"/api/employees/{world.emp.id}/intro-photo?exp=1&sig={views.photo_sig(world.emp.id, 1)}"
    assert (await client.get(expired)).status_code == 403
    other = url.replace(f"/employees/{world.emp.id}/", f"/employees/{world.other.id}/")
    assert (await client.get(other)).status_code == 403  # подпись привязана к новичку

    base = f"/api/employees/{world.emp.id}/intro-photo"
    assert (await client.get(base)).status_code == 401  # без подписи — обычный вход
    assert (await client.get(base, headers=auth(world.hr))).status_code == 200
    assert (await client.get(base, headers=auth(world.mgr2))).status_code == 404  # чужой новичок


async def test_intro_photo_outside_roots_or_token(client, world, monkeypatch, tmp_path):
    monkeypatch.setattr(views, "PHOTO_ROOTS", (tmp_path / "uploads",))
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"synthetic")
    _intro(world.emp, str(secret))  # файл вне data/ и assets/ не отдаём
    r = await client.get(f"/api/employees/{world.emp.id}/intro-photo", headers=auth(world.hr))
    assert r.status_code == 404

    _intro(world.emp, "max-photo-token-synthetic")
    c = await _card(client, world)
    assert c["intro"]["photo_url"].startswith(f"/api/employees/{world.emp.id}/intro-photo?")
    r = await client.get(c["intro"]["photo_url"])
    assert r.status_code == 404 and "МАКС" in r.json()["detail"]


async def test_intro_photo_without_intro(client, world):
    r = await client.get(f"/api/employees/{world.emp.id}/intro-photo", headers=auth(world.hr))
    assert r.status_code == 404


# --- день выхода: выходной переносится на рабочий -----------------------------------

async def test_first_day_moves_from_weekend(client, world, session):
    from app.scenario.workcal import next_workday

    today = datetime.now(MSK).date()
    sunday = today + timedelta(days=(6 - today.weekday()) or 7)
    world.emp.start_date = sunday
    world.emp.probation_end = sunday + timedelta(days=90)
    await session.flush()
    c = await _card(client, world)
    assert c["start_date"] == sunday.isoformat()
    assert c["first_day"] == next_workday(sunday).isoformat() != sunday.isoformat()
    short = next(e for e in (await client.get("/api/employees", headers=auth(world.hr))).json()
                 if e["id"] == world.emp.id)
    assert short["first_day"] == c["first_day"]
