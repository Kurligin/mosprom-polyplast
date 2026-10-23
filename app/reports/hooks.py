"""Еженедельная сводка в планировщике: понедельник, 09:00 по Москве."""

from __future__ import annotations

import logging

from app.jobs import job

log = logging.getLogger(__name__)


@job("weekly_report", cron={"day_of_week": "mon", "hour": 9, "minute": 0})
async def weekly_report() -> None:
    from app.db.session import async_session
    from app.reports import weekly

    async with async_session() as session:
        sent = await weekly.send(session)
        await session.commit()
    log.info("Еженедельная сводка: %s", "отправлена" if sent else "не отправлена")
