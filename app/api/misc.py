"""Роль, площадки, частые темы, демо: перемотка времени и сводка."""

from __future__ import annotations

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.auth import HrDep, StaffDep, role_of
from app.api.deps import SessionDep, call, need, optional
from app.api.views import topics as topics_view
from app.config import settings
from app.db.models import Employee, Site
from app.tickets.service import clean_name, employee_day

router = APIRouter()


@router.get("/me")
async def me(staff: StaffDep) -> dict:
    return {"role": role_of(staff), "name": clean_name(staff.full_name) or staff.full_name, "id": staff.id}


def _site(s: Site) -> dict:
    return {"code": s.code, "name": s.name, "tz": s.tz}


@router.get("/sites")
async def list_sites(staff: StaffDep, session: SessionDep) -> list:
    return [_site(s) for s in await session.scalars(select(Site).order_by(Site.code))]


class SiteInput(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(default="", max_length=200)
    tz: str = Field(default="")


@router.post("/sites")
async def create_site(body: SiteInput, staff: HrDep, session: SessionDep) -> dict:
    """Новая площадка (после загрузки сценария с новым тегом) или правка названия и часового пояса."""
    code, name, tz = body.code.strip(), body.name.strip(), body.tz.strip() or settings.tz_default
    confirm = optional("app.scenario.store", "confirm_site")
    if confirm is not None:
        try:
            site = await call(confirm, session, code, name or code, tz)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    else:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", code):
            raise HTTPException(400, "Код площадки: латиница, цифры, «_» и «-», до 32 символов")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(400, f"Неизвестный часовой пояс {tz}") from None
        site = await session.get(Site, code)
        if site is None:
            site = Site(code=code, name=name or code, tz=tz)
            session.add(site)
        else:
            site.name, site.tz = name or site.name, tz
    await session.commit()
    return _site(site)


@router.get("/topics")
async def topics(staff: HrDep, session: SessionDep) -> list:
    return await topics_view(session)


class ShiftInput(BaseModel):
    to: Literal["-2", "1", "3", "14", "60", "end", "now"]
    employee_id: int | None = None


def _demo_on() -> None:
    """DEMO_ENABLED=false (прод) выключает демо-кнопки целиком."""
    if not settings.demo_enabled:
        raise HTTPException(404, "Демо-режим выключен (DEMO_ENABLED=false)")


@router.post("/demo/shift")
async def demo_shift(body: ShiftInput, staff: HrDep,
                     session: SessionDep) -> dict:
    """Машина времени — только для тестовых и синтетических новичков.

    У живого человека перемотка безвозвратно пропускает шаги сценария и сразу шлёт ему опросы.
    """
    _demo_on()
    fn = need("app.scenario.engine", "time_travel", "Движок сценария")
    q = select(Employee).options(selectinload(Employee.site))
    if body.employee_id is not None:
        emp = await session.scalar(q.where(Employee.id == body.employee_id))
    else:
        # первый тестовый новичок, подключённый к боту — его чат видно на демо
        emp = await session.scalar(q.where(Employee.is_test)
                                   .order_by(Employee.max_user_id.is_(None), Employee.id).limit(1))
    if emp is None:
        raise HTTPException(404, "Тестовый новичок не найден. Запустите make seed.")
    if not (emp.is_test or emp.is_synthetic):
        raise HTTPException(403, "Перематывать время можно только тестовым и синтетическим новичкам")
    result = await call(fn, session, emp, body.to) or {}
    await session.commit()
    day = result.get("day")
    return {
        "employee_id": int(result.get("employee_id") or emp.id),
        "day": int(day) if day is not None else employee_day(emp),
        "message": str(result.get("message") or f"{clean_name(emp.full_name)}: день {employee_day(emp)}"),
    }


@router.post("/demo/report")
async def demo_report(staff: HrDep, session: SessionDep) -> dict:
    _demo_on()
    send = need("app.reports.weekly", "send", "Еженедельная сводка")
    ok = bool(await call(send, session))
    await session.commit()
    if ok:
        return {"ok": True, "message": "Сводка отправлена главе подбора в «МАКС»"}
    return {"ok": False, "message": "Сводку не отправили: глава подбора ещё не подключился к боту или бот выключен"}
