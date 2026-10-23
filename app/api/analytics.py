"""Аналитика для руководителя HR и HR: сводка и зоны риска. Руководителю подразделения — 403.

GET /api/analytics          — воронка этапов, настроение по площадкам и неделям, кластеры тем, число рисков;
GET /api/analytics/risks    — новички в зоне риска с причинами и ссылкой на карточку.
Параметр site=<код> — только одна площадка. Расчёт — app/analytics.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import risks, summary
from app.api.auth import StaffDep, role_of
from app.api.deps import SessionDep
from app.db.models import Staff

router = APIRouter()


async def analytics_staff(staff: StaffDep) -> Staff:
    """HR и руководитель HR. Руководитель подразделения аналитику по всем новичкам не видит."""
    if role_of(staff) not in ("hr", "head"):
        raise HTTPException(403, "Аналитика доступна HR и руководителю HR")
    return staff


AnalyticsDep = Annotated[Staff, Depends(analytics_staff)]
SiteQuery = Annotated[str | None, Query(max_length=32, description="код площадки")]


@router.get("/analytics")
async def analytics(staff: AnalyticsDep, session: SessionDep, site: SiteQuery = None) -> dict:
    return await summary(session, site=site or None)


@router.get("/analytics/risks")
async def analytics_risks(staff: AnalyticsDep, session: SessionDep, site: SiteQuery = None,
                          level: Literal["high", "medium"] | None = None) -> list[dict]:
    items = await risks(session, site=site or None)
    return [i.to_dict() for i in items if level is None or i.level == level]
