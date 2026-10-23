"""Еженедельная сводка в планировщике: понедельник, 09:00 по Москве, и догон, если в это время приложение не работало.

Обе задачи зовут `weekly.send_due`: неделя уходит один раз, отправленная хранится в `Setting`.
"""

from __future__ import annotations

import logging

from app.jobs import job

log = logging.getLogger(__name__)

CATCHUP_SECONDS = 30 * 60


async def _send_due(reason: str) -> None:
    from app.db.session import async_session
    from app.reports import weekly

    async with async_session() as session:
        sent = await weekly.send_due(session)
        await session.commit()
    if sent:
        log.info("Еженедельная сводка отправлена (%s)", reason)


@job("weekly_report", cron={"day_of_week": "mon", "hour": 9, "minute": 0})
async def weekly_report() -> None:
    await _send_due("по расписанию")


@job("weekly_report_catchup", seconds=CATCHUP_SECONDS)
async def weekly_report_catchup() -> None:
    """Догон: перезапуск или деплой в понедельник в 09:00 не оставляет руководителя HR без сводки."""
    await _send_due("догон")
