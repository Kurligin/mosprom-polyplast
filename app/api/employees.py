"""Новички: список, карточка, «Завести новичка», отмашки рекрутера, «План готов», «Провёл встречу»."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import HrDep, StaffDep, current_staff, role_of
from app.api.deps import SessionDep, call, need, optional, save_upload
from app.api.views import (
    EmployeeViews,
    check_photo_sig,
    intro_photo_file,
    invite_link,
    is_external_url,
)
from app.db.models import Employee, ManagerTask, Site, Staff, new_code
from app.tickets.service import clean_name

log = logging.getLogger(__name__)

router = APIRouter()


async def load_employee(session: AsyncSession, emp_id: int, staff: Staff) -> Employee:
    """Новичок с площадкой. Руководителю — только свой."""
    emp = await session.scalar(select(Employee).where(Employee.id == emp_id).options(selectinload(Employee.site)))
    if emp is None or (role_of(staff) == "manager" and emp.manager_id != staff.id):
        raise HTTPException(404, "Новичок не найден")
    return emp


async def employee_card(session: AsyncSession, emp: Employee, staff: Staff) -> dict:
    views = EmployeeViews(session, role_of(staff))
    await views.prepare([emp])
    return await views.full(emp)


@router.get("/employees")
async def list_employees(staff: StaffDep, session: SessionDep, mine: int = 0) -> list[dict]:
    q = select(Employee).options(selectinload(Employee.site)).order_by(Employee.start_date.desc(), Employee.id)
    if role_of(staff) == "manager" or mine:
        q = q.where(Employee.manager_id == staff.id)
    emps = list(await session.scalars(q))
    views = EmployeeViews(session, role_of(staff))
    await views.prepare(emps)
    return [await views.short(e) for e in emps]


@router.get("/employees/{emp_id}")
async def get_employee(emp_id: int, staff: StaffDep,
                       session: SessionDep) -> dict:
    emp = await load_employee(session, emp_id, staff)
    await _ensure_meetings(session, emp)
    return await employee_card(session, emp, staff)


@router.get("/employees/{emp_id}/intro-photo")
async def intro_photo(emp_id: int, request: Request, session: SessionDep,
                      exp: int = 0, sig: str = "", v: str = ""):
    """Фото «представления коллегам» (emp.dialog_data["intro"]["photo_url"]).

    Вход — как у остального API (заголовок initData) или подписанная ссылка из карточки: <img> не шлёт заголовки.
    Файл из data/ или assets/ — отдаём файлом, внешний адрес «МАКС» — переадресацией.
    Токен «МАКС» без адреса скачать нельзя: фото есть только в чате.
    """
    if sig:
        if not check_photo_sig(emp_id, exp, sig):
            raise HTTPException(403, "Ссылка на фото устарела, откройте карточку заново")
        emp = await session.get(Employee, emp_id)
        if emp is None:
            raise HTTPException(404, "Новичок не найден")
    else:
        emp = await load_employee(session, emp_id, await current_staff(request, session))
    intro = (emp.dialog_data or {}).get("intro")
    photo = str((intro or {}).get("photo_url") or "").strip() if isinstance(intro, dict) else ""
    if not photo:
        raise HTTPException(404, "Новичок ещё не прислал фото")
    if is_external_url(photo):
        return RedirectResponse(photo, status_code=307)
    path = intro_photo_file(photo)
    if path is None:
        raise HTTPException(404, "Фото есть только в чате «МАКС»")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def _ensure_meetings(session: AsyncSession, emp: Employee) -> None:
    """Встречи с руководителем по плану, чтобы у карточки были настоящие id (иначе их создаст задача раз в 5 минут)."""
    ensure = optional("app.manager.service", "ensure_tasks")
    if ensure is None or not emp.manager_id:
        return
    try:
        await call(ensure, session, emp, None)
        await session.commit()
    except Exception as e:  # noqa: BLE001 — гонка с фоновой задачей: карточка всё равно покажет встречи
        log.warning("manager.ensure_tasks(%s): %s", emp.id, e)
        await session.rollback()


class NewEmployeeInput(BaseModel):
    full_name: str = Field(min_length=3, max_length=200)
    position: str = Field(default="", max_length=200)
    site: str
    work_type: Literal["office", "production"] = "office"
    start_date: date
    probation_days: int = Field(default=90, ge=7, le=365)
    manager_id: int | None = None
    recruiter_id: int | None = None


def _probation_end(start: date, days: int) -> date:
    """Последний день испытательного: день выхода — первый (как в engine.probation_days)."""
    first_day = optional("app.scenario.engine", "first_day")
    first = start
    if first_day is not None:
        try:
            first = first_day(Employee(start_date=start))
        except Exception as e:  # noqa: BLE001
            log.debug("engine.first_day: %s", e)
    return first + timedelta(days=days - 1)


def _first_name(full_name: str) -> str:
    parts = clean_name(full_name).split()
    return parts[1] if len(parts) >= 2 else (parts[0] if parts else full_name)


@router.post("/employees")
async def create_employee(body: NewEmployeeInput, staff: HrDep,
                          session: SessionDep) -> dict:
    full_name = " ".join(body.full_name.split())
    if len(full_name.split()) < 2:
        raise HTTPException(400, "Укажите фамилию и имя")
    site = await session.get(Site, body.site)
    if site is None:
        raise HTTPException(400, f"Площадки {body.site} нет. Добавьте её в базе или загрузите сценарий.")
    manager = await session.get(Staff, body.manager_id) if body.manager_id else None
    if body.manager_id and (manager is None or manager.role != "manager"):
        raise HTTPException(400, "Руководитель не найден")
    recruiter = await session.get(Staff, body.recruiter_id) if body.recruiter_id else None
    if body.recruiter_id and (recruiter is None or recruiter.role not in ("recruiter", "hr")):
        raise HTTPException(400, "Рекрутер не найден")
    if recruiter is None and staff.role in ("recruiter", "hr"):
        recruiter = staff  # закрепляем того, кто заводит

    code = new_code()
    while await session.scalar(select(Employee.id).where(Employee.invite_code == code)):
        code = new_code()
    emp = Employee(
        full_name=full_name, first_name=_first_name(full_name), site_code=site.code, work_type=body.work_type,
        start_date=body.start_date, probation_end=_probation_end(body.start_date, body.probation_days),
        manager_id=manager.id if manager else None, recruiter_id=recruiter.id if recruiter else None,
        invite_code=code, status="waiting",
        dialog_data={"position": body.position.strip()} if body.position.strip() else {},
    )
    session.add(emp)
    await session.flush()
    # встречи с руководителем сразу, чтобы в Mini App были настоящие id; [] — новичок новый, встреч ещё нет
    ensure = optional("app.manager.service", "ensure_tasks")
    if ensure is not None and emp.manager_id:
        try:
            await call(ensure, session, emp, [])
        except Exception as e:  # noqa: BLE001 — встречи досоздаст фоновая задача
            log.warning("manager.ensure_tasks(%s): %s", emp.id, e)
    await session.commit()
    emp = await load_employee(session, emp.id, staff)
    views = EmployeeViews(session, role_of(staff))
    await views.prepare([emp])
    return {"employee": await views.short(emp), "invite_link": invite_link(emp.invite_code),
            "code": emp.invite_code}


class GateInput(BaseModel):
    gate: Literal["recruiter_go"]


@router.post("/employees/{emp_id}/gate")
async def open_gate(emp_id: int, body: GateInput, staff: HrDep,
                    session: SessionDep) -> dict:
    fn = need("app.scenario.engine", "open_gate", "Движок сценария")
    emp = await load_employee(session, emp_id, staff)
    await call(fn, session, emp, body.gate)
    await session.commit()
    return await employee_card(session, emp, staff)


@router.post("/employees/{emp_id}/plan")
async def plan_ready(emp_id: int, file: Annotated[UploadFile, File()], staff: StaffDep,
                     session: SessionDep) -> dict:
    fn = need("app.scenario.engine", "open_gate", "Движок сценария")
    emp = await load_employee(session, emp_id, staff)
    path = await save_upload(file, "plans", (".pdf", ".docx", ".doc", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"))
    await call(fn, session, emp, "plan_ready", file_path=str(path))
    await session.commit()
    return await employee_card(session, emp, staff)


@router.post("/meetings/{task_id}/done")
async def meeting_done(task_id: int, staff: StaffDep,
                       session: SessionDep) -> dict:
    fn = need("app.manager.service", "mark_done", "Модуль руководителя")
    task = await session.get(ManagerTask, task_id)
    if task is None:
        raise HTTPException(404, "Встреча не найдена")
    emp = await load_employee(session, task.employee_id, staff)
    if role_of(staff) == "manager" and task.manager_id != staff.id:
        raise HTTPException(403, "Это встреча другого руководителя")
    try:
        await call(fn, session, task, staff)
    except PermissionError as e:
        raise HTTPException(403, str(e) or "Это встреча другого руководителя") from None
    await session.commit()
    return await employee_card(session, emp, staff)


@router.get("/staff")
async def list_staff(staff: StaffDep, session: SessionDep,
                     role: Annotated[Literal["manager", "recruiter", "hr", "head"] | None, Query()] = None) -> list:
    roles = {"manager": ("manager",), "recruiter": ("recruiter", "hr"), "hr": ("hr", "recruiter"),
             "head": ("head_recruitment",)}.get(role or "", None)
    q = select(Staff).order_by(Staff.full_name)
    if roles:
        q = q.where(Staff.role.in_(roles))
    return [{"id": s.id, "name": clean_name(s.full_name) or s.full_name, "role": s.role,
             "site": s.site_code, "connected": s.max_user_id is not None} for s in await session.scalars(q)]
