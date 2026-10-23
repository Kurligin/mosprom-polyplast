"""Новички, справочники, встречи, база, демо — на реальной БД в транзакции."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import (
    Employee,
    Flag,
    HrAnswer,
    KbDocument,
    ManagerTask,
    Survey,
    SurveyAnswer,
    SurveyQuestion,
    Ticket,
)
from app.tickets.service import employee_day, probation_days
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio


async def test_list_employees_fields(client, world):
    r = await client.get("/api/employees", headers=auth(world.hr))
    assert r.status_code == 200
    e = next(x for x in r.json() if x["id"] == world.emp.id)
    assert e["full_name"] == "Синтетиков Демьян"
    assert e["position"] == "Оператор экструдера"
    assert e["site"] == world.site.code
    assert e["day"] == employee_day(world.emp) >= 4
    assert e["probation_days"] == probation_days(world.emp) >= 88
    assert e["connected"] is True and e["synthetic"] is True
    other = next(x for x in r.json() if x["id"] == world.other.id)
    assert other["connected"] is False


async def test_card_contacts_meetings_tickets_surveys(client, world, session):
    emp = world.emp
    session.add_all([
        ManagerTask(manager_id=world.mgr.id, employee_id=emp.id, day=3, kind="meeting", status="done",
                    due_date=emp.start_date + timedelta(days=2)),
        ManagerTask(manager_id=world.mgr.id, employee_id=emp.id, day=30, kind="meeting",
                    due_date=emp.start_date + timedelta(days=29)),
        Ticket(employee_id=emp.id, source="hr_command", text="Когда аванс?", status="new"),
        Ticket(employee_id=emp.id, source="unknown", text="Где спецобувь?", status="closed"),
    ])
    sv = Survey(code=f"t_{world.site.code}", title="Первые дни (синтетика)")
    session.add(sv)
    await session.flush()
    q = SurveyQuestion(survey_id=sv.id, kind="scale", text="Оцени день")
    session.add(q)
    await session.flush()
    session.add(SurveyAnswer(employee_id=emp.id, question_id=q.id, score=8, is_synthetic=True))
    await session.flush()

    c = (await client.get(f"/api/employees/{emp.id}", headers=auth(world.hr))).json()
    assert c["manager"]["id"] == world.mgr.id and c["manager"]["name"] == "Начальников Пётр Андреевич"
    assert c["recruiter"]["id"] == world.rec.id
    assert [m["day"] for m in c["meetings"]][:2] == [3, 30]  # остальные встречи досоздал план руководителя
    assert c["next_meeting"]["day"] == 30 and c["next_meeting"]["done"] is False
    assert c["open_tickets"] == 1 and len(c["tickets"]) == 2
    assert c["surveys"] == [{"id": sv.id, "title": "Первые дни (синтетика)", "day": c["surveys"][0]["day"],
                             "status": "done", "score": 8}]
    assert c["invite_link"] == f"https://max.ru/korsik_test_bot?start={emp.invite_code}"
    assert c["gates"] == {"recruiter_go": False, "plan_ready": False}
    assert c["intro"] is None and c["intro_text"] is None


async def test_intro_from_dialog_data(client, world):
    world.emp.dialog_data = {**world.emp.dialog_data,
                             "intro": {"text": "Всем привет! (синтетика)", "photo_url": None, "at": "2026-10-20"}}
    c = (await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.hr))).json()
    assert c["intro_text"] == "Всем привет! (синтетика)"
    assert c["intro"]["at"] == "2026-10-20"


async def test_red_flag_from_flag(client, world, session):
    session.add(Flag(employee_id=world.emp.id, level="red", topic="safety", quote="синтетика", source="survey"))
    await session.flush()
    e = next(x for x in (await client.get("/api/employees", headers=auth(world.hr))).json()
             if x["id"] == world.emp.id)
    assert e["red_flag"] is True


async def test_create_employee(client, world, session):
    start = date.today() + timedelta(days=3)
    body = {"full_name": "Новикова  Синтия Петровна", "position": "Лаборант ОТК", "site": world.site.code,
            "work_type": "production", "start_date": start.isoformat(), "probation_days": 60,
            "manager_id": world.mgr.id, "recruiter_id": world.rec.id}
    r = await client.post("/api/employees", headers=auth(world.hr), json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["invite_link"] == f"https://max.ru/korsik_test_bot?start={data['code']}"
    e = data["employee"]
    assert e["full_name"] == "Новикова Синтия Петровна" and e["position"] == "Лаборант ОТК"
    assert e["day"] <= -2 and e["probation_days"] == 60 and e["connected"] is False
    emp = await session.get(Employee, e["id"])
    assert emp.first_name == "Синтия" and emp.invite_code == data["code"]
    # встречи с руководителем — сразу, с настоящими id
    tasks = (await session.scalars(select(ManagerTask).where(ManagerTask.employee_id == emp.id))).all()
    assert tasks and e["next_meeting"]["id"] in {t.id for t in tasks}


async def test_create_employee_validation(client, world):
    base = {"full_name": "Тестов Тест", "position": "", "site": world.site.code, "work_type": "office",
            "start_date": date.today().isoformat(), "probation_days": 90, "manager_id": world.mgr.id,
            "recruiter_id": world.rec.id}
    assert (await client.post("/api/employees", headers=auth(world.hr), json={**base, "site": "NOPE"})).status_code == 400
    assert (await client.post("/api/employees", headers=auth(world.hr),
                              json={**base, "manager_id": world.rec.id})).status_code == 400
    assert (await client.post("/api/employees", headers=auth(world.hr), json={**base, "full_name": "Один"})
            ).status_code == 400
    assert (await client.post("/api/employees", headers=auth(world.mgr), json=base)).status_code == 403


async def test_meeting_done(client, world, session):
    t = ManagerTask(manager_id=world.mgr.id, employee_id=world.emp.id, day=3, kind="meeting",
                    due_date=world.emp.start_date + timedelta(days=2))
    session.add(t)
    await session.flush()
    # чужой руководитель не видит новичка
    r = await client.post(f"/api/meetings/{t.id}/done", headers=auth(world.mgr2), json={})
    assert r.status_code == 404
    r = await client.post(f"/api/meetings/{t.id}/done", headers=auth(world.mgr), json={})
    assert r.status_code == 200, r.text
    m = next(x for x in r.json()["meetings"] if x["id"] == t.id)
    assert m["done"] is True
    assert (await session.get(ManagerTask, t.id)).status == "done"


async def test_sites_and_staff(client, world):
    sites = (await client.get("/api/sites", headers=auth(world.hr))).json()
    assert {"code": world.site.code, "name": "Тестовая площадка (синтетика)", "tz": "Europe/Moscow"} in sites
    r = await client.post("/api/sites", headers=auth(world.hr),
                          json={"code": f"N{world.site.code}", "name": "Новая (синтетика)", "tz": "Asia/Yekaterinburg"})
    assert r.status_code == 200 and r.json()["tz"] == "Asia/Yekaterinburg"
    bad = await client.post("/api/sites", headers=auth(world.hr), json={"code": "X", "name": "", "tz": "Mars/Base"})
    assert bad.status_code == 400
    managers = (await client.get("/api/staff?role=manager", headers=auth(world.hr))).json()
    ids = {m["id"] for m in managers}
    assert world.mgr.id in ids and world.rec.id not in ids
    recruiters = {m["id"] for m in (await client.get("/api/staff?role=recruiter", headers=auth(world.hr))).json()}
    assert world.rec.id in recruiters and world.mgr.id not in recruiters


async def test_kb_documents_and_confirm(client, world, session, monkeypatch):
    from app.kb import ingest

    doc = KbDocument(title="Регламент (синтетика)", doc_type="md", status="confirmed")
    ha = HrAnswer(question="Где получить вторую пару спецобуви?", answer="На складе СИЗ, корпус 2 (синтетика).",
                  site_code=world.site.code, author_id=world.hr.id, status="draft")
    session.add_all([doc, ha])
    await session.flush()
    docs = (await client.get("/api/kb/documents", headers=auth(world.hr))).json()
    d = next(x for x in docs if x["kind"] != "answer" and x["id"] == doc.id)
    assert d["kind"] == "pdf" and d["fragments"] == 0
    a = next(x for x in docs if x["kind"] == "answer" and x["id"] == ha.id)
    assert a["status"] == "draft" and a["site"] == world.site.code

    calls = []

    async def fake_index(s, hr_answer):
        calls.append(hr_answer.status)

    monkeypatch.setattr(ingest, "index_hr_answer", fake_index)
    r = await client.post(f"/api/kb/answers/{ha.id}/confirm", headers=auth(world.hr), json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["kind"] == "answer"
    assert calls == ["confirmed"]


async def test_kb_upload(client, world, monkeypatch, tmp_path):
    from app.api import deps
    from app.kb import ingest

    monkeypatch.setattr(deps, "UPLOAD_ROOT", tmp_path)
    seen = {}

    async def fake_ingest(s, path, *, title, is_kt, site_codes, uploaded_by):
        seen.update(path=path, title=title, is_kt=is_kt, site_codes=site_codes, uploaded_by=uploaded_by)
        d = KbDocument(title=title, doc_type="pdf", is_kt=is_kt, site_codes=site_codes, status="confirmed")
        s.add(d)
        await s.flush()
        return d, 42

    monkeypatch.setattr(ingest, "ingest_file", fake_ingest)
    r = await client.post("/api/kb/documents", headers=auth(world.hr),
                          files={"file": ("Памятка.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
                          data={"title": "Памятка (синтетика)", "is_kt": "true", "site_codes": world.site.code})
    assert r.status_code == 200, r.text
    assert r.json()["chunks"] == 42 and r.json()["trade_secret"] is True and r.json()["site"] == world.site.code
    assert seen["path"].exists() and seen["path"].parent == tmp_path / "kb"
    assert seen["uploaded_by"] == world.hr.id

    r = await client.post("/api/kb/documents", headers=auth(world.hr),
                          files={"file": ("s.xlsx", b"PK", "application/octet-stream")})
    assert r.status_code == 400


async def test_topics(client, world, session):
    from app.db.models import QuestionLog

    for _ in range(3):
        session.add(QuestionLog(employee_id=world.emp.id, question="Когда аванс? (синтетика)", route="answer",
                                topic="зарплата"))
    session.add(QuestionLog(employee_id=world.emp.id, question="Мне не пришла зарплата", route="personal"))
    await session.flush()
    topics = (await client.get("/api/topics", headers=auth(world.hr))).json()
    t = next(x for x in topics if x["topic"] == "Зарплата")
    assert t["count"] >= 3 and t["peak_days"].startswith(("день", "дни"))
    assert any(x["topic"] == "Личные вопросы" for x in topics)


async def test_demo_shift_and_report(client, world, session):
    from app.api.deps import optional

    world.emp.is_test = True
    await session.flush()
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "3", "employee_id": world.emp.id})
    if optional("app.scenario.engine", "time_travel") is None:
        assert r.status_code == 501
    else:
        assert r.status_code == 200, r.text
        assert r.json()["employee_id"] == world.emp.id and r.json()["day"] >= 3
        assert world.emp.time_shift_hours != 0  # день 5 → день 3: назад
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "7"})
    assert r.status_code == 422
    r = await client.post("/api/demo/report", headers=auth(world.hr), json={})
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        assert set(r.json()) == {"ok", "message"}


async def test_scenario_import(client, world, monkeypatch, tmp_path):
    from app.api import deps
    from app.scenario import store

    monkeypatch.setattr(deps, "UPLOAD_ROOT", tmp_path)

    async def fake_import(s, path):
        assert path.suffix == ".xlsx" and path.exists()
        return store.ImportReport(steps=68, by_site={"GTP": 60}, surveys=5, attachments=12, new_sites=["NEW1"],
                                  warnings=["строка 70: нет времени"])

    monkeypatch.setattr(store, "import_xlsx", fake_import)
    r = await client.post("/api/scenario/import", headers=auth(world.hr),
                          files={"file": ("scenario.xlsx", b"PK synthetic", "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json() == {"steps": 68, "by_site": {"GTP": 60}, "surveys": 5, "attachments": 12,
                        "new_sites": ["NEW1"], "warnings": ["строка 70: нет времени"]}
    r = await client.post("/api/scenario/import", headers=auth(world.hr),
                          files={"file": ("scenario.csv", b"a,b", "text/csv")})
    assert r.status_code == 400


async def test_gate_and_plan(client, world, monkeypatch, tmp_path):
    from app.api import deps

    monkeypatch.setattr(deps, "UPLOAD_ROOT", tmp_path)
    r = await client.post(f"/api/employees/{world.emp.id}/gate", headers=auth(world.hr), json={"gate": "recruiter_go"})
    assert r.status_code == 200, r.text
    assert r.json()["gates"]["recruiter_go"] is True
    assert (await client.post(f"/api/employees/{world.emp.id}/gate", headers=auth(world.mgr),
                              json={"gate": "recruiter_go"})).status_code == 403
    r = await client.post(f"/api/employees/{world.emp.id}/plan", headers=auth(world.mgr),
                          files={"file": ("План адаптации.pdf", b"%PDF-1.4 synthetic", "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["gates"]["plan_ready"] is True
    saved = world.emp.dialog_data["gates"]["plan_ready"]["file"]
    assert saved.startswith(str(tmp_path / "plans"))
    assert (await client.post(f"/api/employees/{world.emp.id}/plan", headers=auth(world.mgr2),
                              files={"file": ("p.pdf", b"%PDF", "application/pdf")})).status_code == 404


async def test_plan_step_time_and_sent(client, world, session, monkeypatch, tmp_path):
    """«План готов» не значит «отправлен»: карточка знает, когда уйдёт шаг плана и ушёл ли он."""
    from app.api import deps
    from app.db.models import Delivery
    from app.scenario import engine

    monkeypatch.setattr(deps, "UPLOAD_ROOT", tmp_path)
    c = (await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.hr))).json()
    assert c["plan_step"]["sent"] is False
    r = await client.post(f"/api/employees/{world.emp.id}/plan", headers=auth(world.hr),
                          files={"file": ("План.pdf", b"%PDF-1.4 synthetic", "application/pdf")})
    assert r.status_code == 200, r.text
    step = r.json()["plan_step"]
    assert step["sent"] is False and step["at"]

    plan = await engine.load_plan(session)
    spec = next(s for s in plan.specs_for(world.emp) if s.gate == "plan_ready")
    row = plan.by_number[spec.number]
    session.add(Delivery(employee_id=world.emp.id, step_id=row.id, scheduled_at=datetime.fromisoformat(step["at"]),
                         status="sent", idempotency_key=f"test-plan-{world.emp.id}"))
    await session.flush()
    c = (await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.hr))).json()
    assert c["plan_step"]["sent"] is True


async def test_hr_views_panel_as_manager(client, world, session):
    """«Посмотреть глазами руководителя» в «МАКС»: HR видит список и карточки руководителя, без опросов и обращений."""
    session.add(Ticket(employee_id=world.emp.id, source="hr_command", text="Вопрос (синтетика)", status="new"))
    session.add(Flag(employee_id=world.emp.id, level="red", topic="травля", quote="(синтетика)", source="survey"))
    await session.flush()
    hr_row = next(e for e in (await client.get("/api/employees", headers=auth(world.hr))).json()
                  if e["id"] == world.emp.id)
    assert hr_row["open_tickets"] == 1 and hr_row["red_flag"] is True

    for who in (world.hr, world.head):
        r = await client.get(f"/api/employees?view=manager&manager_id={world.mgr.id}", headers=auth(who))
        assert r.status_code == 200, r.text
        rows = {e["id"]: e for e in r.json()}
        assert world.emp.id in rows and world.other.id not in rows
        row = rows[world.emp.id]
        assert row["open_tickets"] == 0 and row["red_flag"] is False and row["pending_surveys"] == 0

    card = await client.get(f"/api/employees/{world.emp.id}?view=manager&manager_id={world.mgr.id}",
                            headers=auth(world.hr))
    assert card.status_code == 200
    assert card.json()["tickets"] == [] and card.json()["surveys"] == [] and card.json()["meetings"]
    assert (await client.get(f"/api/employees/{world.other.id}?view=manager&manager_id={world.mgr.id}",
                             headers=auth(world.hr))).status_code == 404

    # без manager_id — первый руководитель из базы; список не пустой у живой базы не обязателен
    assert (await client.get("/api/employees?view=manager", headers=auth(world.hr))).status_code == 200
    # руководитель так чужих не увидит
    r = await client.get(f"/api/employees?view=manager&manager_id={world.mgr2.id}", headers=auth(world.mgr))
    assert {e["id"] for e in r.json()} == {world.emp.id}


async def test_head_role_named_hr_lead():
    from app.api.views import ROLE_LABELS
    from app.bot.texts import ROLE_NAMES

    assert ROLE_LABELS["head_recruitment"] == "Руководитель HR"
    assert ROLE_NAMES["head_recruitment"] == "руководитель HR"


async def test_create_employee_refreshes_name_masking(client, world, monkeypatch):
    """ФИО нового человека сразу попадает в маскирование перед LLM: кэш имён сбрасывается после коммита."""
    from app.privacy import names

    calls = []
    monkeypatch.setattr(names, "invalidate", lambda: calls.append(1))
    body = {"full_name": "Маскова Синтия", "position": "", "site": world.site.code, "work_type": "office",
            "start_date": (date.today() + timedelta(days=3)).isoformat(), "probation_days": 90,
            "manager_id": world.mgr.id, "recruiter_id": world.rec.id}
    r = await client.post("/api/employees", headers=auth(world.hr), json=body)
    assert r.status_code == 200, r.text
    assert calls


async def test_list_marks_test_newbies(client, world, session):
    """Машину времени можно только тестовым: список отдаёт признак test, живые люди в «Кого перематываем» не попадут."""
    world.other.is_test = True
    await session.flush()
    rows = {e["id"]: e for e in (await client.get("/api/employees", headers=auth(world.hr))).json()}
    assert rows[world.other.id]["test"] is True and rows[world.emp.id]["test"] is False
