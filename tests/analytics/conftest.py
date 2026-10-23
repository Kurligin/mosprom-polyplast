"""Фикстуры аналитики: реальная БД в транзакции с откатом, своя площадка — чтобы не видеть живую синтетику.

Все люди — синтетические.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models import Employee, Site, Staff


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield s
        finally:
            await s.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def world(session):
    """Площадка, руководитель и рекрутер; new(...) — завести новичка на этой площадке."""
    base = random.randint(10**12, 9 * 10**12)
    site = Site(code=f"A{base % 10**8}", name="Площадка аналитики (синтетика)", tz="Europe/Moscow")
    session.add(site)
    await session.flush()
    mgr = Staff(full_name="Начальников Пётр Андреевич (руководитель, синтетика)", role="manager",
                site_code=site.code)
    rec = Staff(full_name="Подборова Мария Игоревна (рекрутер, синтетика)", role="recruiter")
    session.add_all([mgr, rec])
    await session.flush()
    n = 0

    async def new(name: str, *, days_ago: int = 10, status: str = "active", probation: int = 90,
                  linked: bool = False, synthetic: bool = True, **kw) -> Employee:
        nonlocal n
        n += 1
        start = date.today() - timedelta(days=days_ago)
        e = Employee(full_name=f"{name} [синтетика]", first_name=name.split()[-1], site_code=site.code,
                     work_type=kw.pop("work_type", "office"), start_date=start,
                     probation_end=start + timedelta(days=probation), manager_id=mgr.id, recruiter_id=rec.id,
                     status=status, is_synthetic=synthetic, max_user_id=(base + n) if linked else None, **kw)
        session.add(e)
        await session.flush()
        return e

    return SimpleNamespace(site=site, mgr=mgr, rec=rec, new=new, session=session)
