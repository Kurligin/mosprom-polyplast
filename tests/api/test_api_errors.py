"""Ветки ошибок API Mini App: 400 / 404 / 409 / 413 / 501 и то, что после ошибки не остаётся мусора.

Реальная БД в транзакции с откатом (tests/api/conftest.py), загрузки — во временную папку. Данные синтетические.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api import deps
from app.api import employees as employees_api
from app.api.kb import _report_dict
from app.api.views import photo_sig
from app.db.models import (
    Employee,
    HrAnswer,
    KbChunk,
    KbDocument,
    ManagerTask,
    ScenarioStep,
    Ticket,
)
from tests.api.conftest import auth

pytestmark = pytest.mark.asyncio


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "UPLOAD_ROOT", tmp_path)
    return tmp_path


def _files(root) -> list:
    return sorted(p.name for p in root.rglob("*") if p.is_file())


def _fake_vec(text: str) -> list[float]:
    v = [0.0] * 1024
    v[sum(map(ord, text)) % 1024] = 1.0
    return v


@pytest.fixture
def fake_embed(monkeypatch):
    """Модель bge-m3 не грузим: детерминированные векторы."""
    from app.kb import embed as E

    async def embed(texts, batch_size=16):
        return [_fake_vec(t) for t in texts]

    monkeypatch.setattr(E, "embed", embed)


# --- база знаний: загрузка документа -------------------------------------------------------------


async def test_kb_upload_unknown_site_saves_nothing(client, world, uploads):
    r = await client.post("/api/kb/documents", headers=auth(world.hr), data={"site_codes": "NOPE, all"},
                          files={"file": ("memo.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400 and r.json()["detail"] == "Площадки NOPE нет"
    assert _files(uploads) == []


@pytest.mark.parametrize("name, data, detail", [
    ("broken.pdf", b"%PDF-1.4 synthetic, not a real pdf", "PDF не открывается"),
    ("broken.pptx", b"PK\x03\x04 not a zip", "PPTX не открывается"),
], ids=["pdf", "pptx"])
async def test_kb_upload_broken_file_is_400_and_removed(client, world, session, uploads, name, data, detail):
    """Битый файл: понятная 400 вместо 500, документа нет, файл с диска убран."""
    before = await session.scalar(select(func.count()).select_from(KbDocument))
    r = await client.post("/api/kb/documents", headers=auth(world.hr), data={"title": "Битый (синтетика)"},
                          files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("Не удалось разобрать файл: ") and detail in r.json()["detail"]
    assert await session.scalar(select(func.count()).select_from(KbDocument)) == before
    assert _files(uploads) == [], "непринятый файл не остаётся в data/uploads"


async def test_kb_upload_empty_file(client, world, uploads):
    r = await client.post("/api/kb/documents", headers=auth(world.hr),
                          files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400 and r.json()["detail"] == "Файл пустой"
    assert _files(uploads) == []


async def test_kb_upload_too_large_removes_partial_file(client, world, uploads, monkeypatch):
    monkeypatch.setattr(deps, "MAX_UPLOAD", 1024)
    r = await client.post("/api/kb/documents", headers=auth(world.hr),
                          files={"file": ("big.pdf", b"%PDF" + b"x" * 5000, "application/pdf")})
    assert r.status_code == 413 and r.json()["detail"] == "Файл больше 50 МБ"
    assert _files(uploads) == []


async def test_kb_upload_without_ingest_module_is_501(client, world, monkeypatch, uploads):
    monkeypatch.setitem(sys.modules, "app.kb.ingest", None)
    r = await client.post("/api/kb/documents", headers=auth(world.hr),
                          files={"file": ("memo.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 501 and "app.kb.ingest.ingest_file" in r.json()["detail"]
    assert _files(uploads) == []


# --- база знаний: подтверждение -----------------------------------------------------------------


async def test_confirm_document(client, world, session):
    doc = KbDocument(title="Тест: черновик статьи (синтетика)", doc_type="article", status="draft")
    session.add(doc)
    await session.flush()
    session.add(KbChunk(document_id=doc.id, path="x", text="Шаг 1 (синтетика).", chunk_key=f"test:{doc.id}",
                        source_label="x"))
    await session.flush()
    r = await client.post(f"/api/kb/documents/{doc.id}/confirm", headers=auth(world.hr))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["chunks"] == 1
    assert (await session.get(KbDocument, doc.id)).status == "confirmed"
    assert (await client.post("/api/kb/documents/999999999/confirm", headers=auth(world.hr))).status_code == 404
    assert (await client.post(f"/api/kb/documents/{doc.id}/confirm", headers=auth(world.mgr))).status_code == 403


async def test_confirm_answer_with_edits_goes_to_search(client, world, session, fake_embed):
    """HR поправил вопрос и ответ при подтверждении: в базу уходит исправленный текст."""
    ha = HrAnswer(question="где спецовка", answer="склад", author_id=world.hr.id, status="draft")
    session.add(ha)
    await session.flush()
    r = await client.post(f"/api/kb/answers/{ha.id}/confirm", headers=auth(world.hr),
                          json={"question": "  Где получить спецовку? ", "answer": " На складе СИЗ (синтетика). "})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    chunk = (await session.scalars(select(KbChunk).where(KbChunk.hr_answer_id == ha.id))).one()
    assert (chunk.path, chunk.text) == ("Где получить спецовку?", "На складе СИЗ (синтетика).")
    assert chunk.embedding is not None

    r = await client.post(f"/api/kb/answers/{ha.id}/confirm", headers=auth(world.hr), json={"question": "   "})
    assert r.status_code == 200 and (await session.get(HrAnswer, ha.id)).question == "Где получить спецовку?", \
        "пустая правка текст не стирает"
    assert (await session.scalar(select(func.count()).where(KbChunk.hr_answer_id == ha.id))) == 1


async def test_confirm_unknown_answer_404(client, world):
    r = await client.post("/api/kb/answers/999999999/confirm", headers=auth(world.hr), json={})
    assert r.status_code == 404 and r.json()["detail"] == "Ответ HR не найден"


# --- сценарий xlsx ----------------------------------------------------------------------------


async def _active_steps(session) -> int:
    return await session.scalar(select(func.count()).select_from(ScenarioStep).where(ScenarioStep.is_active))


async def test_scenario_import_broken_xlsx_400(client, world, session, uploads):
    before = await _active_steps(session)
    r = await client.post("/api/scenario/import", headers=auth(world.hr),
                          files={"file": ("scenario.xlsx", b"PK not a zip", "application/octet-stream")})
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("Не удалось разобрать сценарий: ")
    assert await _active_steps(session) == before
    assert _files(uploads) == []


async def test_scenario_import_without_steps_keeps_scenario(client, world, session, uploads, tmp_path):
    """Таблица без шагов (пустая или чужая) не выключает весь сценарий у всех новичков."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Отчёт по продажам (синтетика)", 1, 2])
    path = tmp_path / "other.xlsx"
    wb.save(path)
    session.add(ScenarioStep(key=f"test-step-{world.site.code}", order_no=1, text="Шаг (синтетика)", is_active=True))
    await session.commit()  # шаг уже в базе до загрузки (внешняя транзакция теста всё равно откатится)
    before = await _active_steps(session)
    assert before >= 1
    r = await client.post("/api/scenario/import", headers=auth(world.hr),
                          files={"file": ("other.xlsx", path.read_bytes(), "application/octet-stream")})
    assert r.status_code == 400, r.text
    assert "шагов сценария" in r.json()["detail"]
    assert await _active_steps(session) == before, "ни один шаг не выключен"
    assert _files(uploads / "scenario") == []


async def test_report_dict_forms():
    @dataclasses.dataclass
    class Rep:
        steps: int = 3

    class Model(BaseModel):
        steps: int = 4

    assert _report_dict(Rep()) == {"steps": 3}
    assert _report_dict(Model()) == {"steps": 4}
    assert _report_dict({"steps": 5}) == {"steps": 5}
    assert _report_dict(SimpleNamespace(steps=6)) == {"steps": 6}


# --- новички и встречи --------------------------------------------------------------------------


async def test_view_as_unknown_manager_404(client, world):
    for mid in (world.hr.id, 999999999):
        r = await client.get(f"/api/employees?view=manager&manager_id={mid}", headers=auth(world.hr))
        assert r.status_code == 404 and r.json()["detail"] == "Руководитель не найден"


async def test_signed_photo_of_deleted_employee_404(client, world):
    exp = 4_000_000_000
    r = await client.get(f"/api/employees/999999999/intro-photo?exp={exp}&sig={photo_sig(999999999, exp)}")
    assert r.status_code == 404
    r = await client.get(f"/api/employees/{world.emp.id}/intro-photo?exp={exp}&sig=bad")
    assert r.status_code == 403


async def test_card_survives_meetings_race(client, world, session, monkeypatch, caplog):
    """Фоновая задача успела записать встречу раньше карточки: уникальный ключ, а карточка всё равно открывается.

    Раньше сбой откатывал всю сессию: поля новичка и сотрудника сбрасывались, ответ падал на MissingGreenlet (500).
    """
    from app.manager import service as manager

    due = world.emp.start_date + timedelta(days=2)
    done_by_job = ManagerTask(manager_id=world.mgr.id, employee_id=world.emp.id, day=3, kind="meeting", due_date=due)
    session.add(done_by_job)
    await session.commit()  # новичок и встреча фоновой задачи уже в базе

    async def race(session, emp, existing=None):
        session.add(ManagerTask(manager_id=emp.manager_id, employee_id=emp.id, day=3, kind="meeting", due_date=due))
        await session.flush()  # IntegrityError: uq_manager_task

    monkeypatch.setattr(manager, "ensure_tasks", race)
    with caplog.at_level(logging.WARNING, logger="app.api.employees"):
        r = await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.hr))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == world.emp.id and r.json()["site"]
    assert done_by_job.id in {m["id"] for m in r.json()["meetings"]}
    assert "uq_manager_task" in caplog.text


async def test_create_employee_recruiter_and_code(client, world, session, monkeypatch):
    from app.manager import service as manager

    base = {"full_name": "Синтетикова  Вера   Павловна", "site": world.site.code, "start_date": date.today().isoformat(),
            "manager_id": world.mgr.id}
    r = await client.post("/api/employees", headers=auth(world.hr), json={**base, "recruiter_id": world.mgr.id})
    assert r.status_code == 400 and r.json()["detail"] == "Рекрутер не найден"
    r = await client.post("/api/employees", headers=auth(world.hr), json={**base, "manager_id": 999999999})
    assert r.status_code == 400 and r.json()["detail"] == "Руководитель не найден"

    # код приглашения уже занят — генерируется новый; сбой встреч не мешает завести новичка
    world.emp.invite_code = "INV-test-taken-1"
    await session.flush()
    codes = iter(["INV-test-taken-1", "INV-test-unique-1"])
    monkeypatch.setattr(employees_api, "new_code", lambda: next(codes))

    async def broken(session, emp, existing=None):
        raise RuntimeError("manager module down")

    monkeypatch.setattr(manager, "ensure_tasks", broken)
    r = await client.post("/api/employees", headers=auth(world.rec), json=base)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "INV-test-unique-1"
    emp = await session.get(Employee, r.json()["employee"]["id"])
    assert emp.recruiter_id == world.rec.id, "рекрутер, который заводит, закрепляется сам"
    assert (emp.full_name, emp.first_name) == ("Синтетикова Вера Павловна", "Вера")


async def test_probation_end_without_engine(monkeypatch):
    def broken(emp):
        raise RuntimeError("engine down")

    from app.scenario import engine

    monkeypatch.setattr(engine, "first_day", broken)
    assert employees_api._probation_end(date(2026, 11, 2), 90) == date(2026, 11, 2) + timedelta(days=89)


async def test_meeting_errors(client, world, session, monkeypatch):
    assert (await client.post("/api/meetings/999999999/done", headers=auth(world.mgr))).status_code == 404
    # новичка передали другому руководителю: старую встречу прежнего руководителя новый не отмечает
    t = ManagerTask(manager_id=world.mgr2.id, employee_id=world.emp.id, day=3, kind="meeting",
                    due_date=world.emp.start_date + timedelta(days=2))
    session.add(t)
    await session.flush()
    r = await client.post(f"/api/meetings/{t.id}/done", headers=auth(world.mgr))
    assert r.status_code == 403 and r.json()["detail"] == "Это встреча другого руководителя"
    assert (await session.get(ManagerTask, t.id)).status != "done"

    from app.manager import service as manager

    async def denied(session, task, staff, **kw):
        raise PermissionError("")

    monkeypatch.setattr(manager, "mark_done", denied)
    r = await client.post(f"/api/meetings/{t.id}/done", headers=auth(world.hr))
    assert r.status_code == 403 and r.json()["detail"] == "Это встреча другого руководителя"


# --- площадки, демо, обращения ------------------------------------------------------------------


async def test_create_site_without_store_module(client, world, session, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.scenario.store", None)
    h = auth(world.hr)
    r = await client.post("/api/sites", headers=h, json={"code": "Новая площадка", "name": "x"})
    assert r.status_code == 400 and "латиница" in r.json()["detail"]
    r = await client.post("/api/sites", headers=h, json={"code": "TST9", "tz": "Mars/Olympus"})
    assert r.status_code == 400 and "Mars/Olympus" in r.json()["detail"]
    r = await client.post("/api/sites", headers=h, json={"code": "TST9"})
    assert r.json() == {"code": "TST9", "name": "TST9", "tz": "Europe/Moscow"}
    r = await client.post("/api/sites", headers=h, json={"code": "TST9", "name": "Тест (синтетика)",
                                                          "tz": "Asia/Yekaterinburg"})
    assert r.json() == {"code": "TST9", "name": "Тест (синтетика)", "tz": "Asia/Yekaterinburg"}


async def test_demo_shift_default_is_connected_test_newbie(client, world, monkeypatch):
    """Без employee_id перематывается первый подключённый тестовый новичок. Перемотка подменена: живого не трогаем."""
    from app.scenario import engine

    got = []

    async def fake_travel(session, emp, to):
        got.append((emp.is_test, emp.max_user_id is not None, to))
        return {}

    monkeypatch.setattr(engine, "time_travel", fake_travel)
    world.emp.is_test = True
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "now"})
    assert r.status_code == 200, r.text
    assert got == [(True, True, "now")]
    assert set(r.json()) == {"employee_id", "day", "message"} and "день" in r.json()["message"]


async def test_demo_shift_unknown_employee_404(client, world):
    r = await client.post("/api/demo/shift", headers=auth(world.hr), json={"to": "1", "employee_id": 999999999})
    assert r.status_code == 404


@pytest.mark.parametrize("sent, ok", [(True, True), (False, False)])
async def test_demo_report_result(client, world, monkeypatch, sent, ok):
    from app.reports import weekly

    async def fake_send(session):
        return sent

    monkeypatch.setattr(weekly, "send", fake_send)
    r = await client.post("/api/demo/report", headers=auth(world.head))
    assert r.status_code == 200 and r.json()["ok"] is ok
    assert ("отправлена" if ok else "не отправили") in r.json()["message"]


async def test_reply_whitespace_is_400(client, world, session):
    from app.bot.repo import SqlRepo
    from app.tickets import service

    t = await service.create_ticket(None, SqlRepo(session), world.emp, "Вопрос (синтетика)", source="hr_command")
    r = await client.post(f"/api/tickets/{t.id}/reply", headers=auth(world.hr), json={"text": "   \n "})
    assert r.status_code == 400 and r.json()["detail"] == "Пустой ответ"
    assert (await session.get(Ticket, t.id)).status == "new"
