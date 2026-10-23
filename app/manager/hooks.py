"""Подключение руководителя к боту и планировщику: кнопка «Провёл» (mg:done:<id>), задача manager_tasks."""

from __future__ import annotations

import logging

from app.bot import hooks
from app.db.models import Employee, ManagerTask
from app.jobs import job
from app.manager import service
from app.manager import texts as T

log = logging.getLogger(__name__)


@hooks.on_callback("mg")
async def manager_button(cb: hooks.Callback, person) -> None:
    parts = cb.payload.split(":")
    if len(parts) != 3 or parts[1] != "done" or not parts[2].isdigit():
        await cb.toast(T.STALE)
        return
    if not cb.is_staff:
        await cb.toast(T.NOT_YOURS)
        return
    session = cb.repo.s
    task = await session.get(ManagerTask, int(parts[2]))
    if task is None:
        await cb.toast(T.NOT_FOUND)
        return
    try:
        await service.mark_done(session, task, person, edit_push=False)
    except PermissionError:
        await cb.toast(T.NOT_YOURS)
        return
    emp = await session.get(Employee, task.employee_id)
    text, atts = service.done_view(task, emp)
    await cb.replace(text, atts, staff=person)


@job("manager_tasks", seconds=300)
async def manager_tasks() -> None:
    from app.db.session import async_session

    async with async_session() as session:
        stats = await service.tick(session)
        await session.commit()
    if stats["created"] or stats["sent"]:
        log.info("manager_tasks: встреч создано %s, push отправлено %s", stats["created"], stats["sent"])
