"""Фикстуры сводки: реальная БД в транзакции с откатом. Все люди — синтетические."""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


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
