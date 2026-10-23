"""Подключение обращений к боту: фабрика тикетов, ответ новичка в открытое обращение, кнопка «Взять себе»."""

from __future__ import annotations

from app.bot import hooks
from app.db.models import Employee, Ticket
from app.tickets import service
from app.tickets import texts as T

hooks.on_create_ticket(service.create_ticket)


@hooks.pre_route
async def reply_window(ctx, repo, emp, text: str, msg: dict) -> bool:
    if getattr(repo, "s", None) is None:
        return False
    return await service.route_reply(ctx, repo, emp, text, msg)


@hooks.on_callback("tk")
async def ticket_button(cb: hooks.Callback, person) -> None:
    """tk:take:<id> — «Взять себе» под уведомлением в HR-группе."""
    parts = cb.payload.split(":")
    if len(parts) != 3 or parts[1] != "take" or not parts[2].isdigit():
        await cb.toast(T.TAKE_NOT_FOUND)
        return
    if not cb.is_staff:
        await cb.toast(T.TAKE_ONLY_STAFF)
        return
    if person.role == "manager":
        await cb.toast(T.TAKE_MANAGER)
        return
    session = cb.repo.s
    ticket = await session.get(Ticket, int(parts[2]))
    if ticket is None:
        await cb.toast(T.TAKE_NOT_FOUND)
        return
    if ticket.status == "closed":
        await cb.toast(T.TAKE_CLOSED)
        return
    await service.take(session, ticket, person, edit_group=False)
    emp = await session.get(Employee, ticket.employee_id)
    text = await service.group_text(session, ticket, emp)
    atts = service.group_buttons(ticket)
    await cb.replace(text, atts, staff=person)
