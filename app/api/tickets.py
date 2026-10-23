"""Обращения: очередь, экран обращения, ответ новичку, «Взять», «Закрыть». Только HR."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import HrDep
from app.api.deps import SessionDep
from app.api.views import sort_tickets, ticket_json, ticket_shorts
from app.db.models import Message, Site, Ticket
from app.tickets import service

router = APIRouter()


async def _ticket(session: AsyncSession, ticket_id: int) -> Ticket:
    t = await session.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404, "Обращение не найдено")
    return t


@router.get("/tickets")
async def list_tickets(staff: HrDep, session: SessionDep,
                       status: Literal["open", "in_progress", "closed"] | None = None) -> list:
    q = select(Ticket)
    if status:
        q = q.where(Ticket.status == service.DB_STATUS[status])
    rows = await ticket_shorts(session, list(await session.scalars(q)))
    return sort_tickets(rows)


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, staff: HrDep,
                     session: SessionDep) -> dict:
    return await ticket_json(session, await _ticket(session, ticket_id))


class ReplyInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    add_to_kb: bool = False
    kb_site: str | None = None


@router.post("/tickets/{ticket_id}/reply")
async def reply(ticket_id: int, body: ReplyInput, staff: HrDep,
                session: SessionDep) -> dict:
    t = await _ticket(session, ticket_id)
    if not body.text.strip():
        raise HTTPException(400, "Пустой ответ")
    if body.add_to_kb and body.kb_site not in (None, "", "all") and await session.get(Site, body.kb_site) is None:
        raise HTTPException(400, f"Площадки {body.kb_site} нет")
    try:
        await service.reply(session, t, staff, body.text, add_to_kb=body.add_to_kb, kb_site=body.kb_site)
    except service.TicketClosed:
        raise HTTPException(409, "Обращение уже закрыто") from None
    await session.commit()
    last = await session.scalar(select(Message).where(Message.ticket_id == t.id, Message.staff_id == staff.id)
                                .order_by(Message.id.desc()).limit(1))
    data = await ticket_json(session, t)
    data["delivered"] = bool(last and last.mid)
    data["added_to_kb"] = bool(body.add_to_kb and not t.is_personal)
    return data


@router.post("/tickets/{ticket_id}/take")
async def take(ticket_id: int, staff: HrDep,
               session: SessionDep) -> dict:
    t = await _ticket(session, ticket_id)
    try:
        await service.take(session, t, staff)
    except service.TicketClosed:
        raise HTTPException(409, "Обращение уже закрыто") from None
    await session.commit()
    return await ticket_json(session, t)


@router.post("/tickets/{ticket_id}/close")
async def close(ticket_id: int, staff: HrDep,
                session: SessionDep) -> dict:
    t = await _ticket(session, ticket_id)
    await service.close(session, t, staff)
    await session.commit()
    return await ticket_json(session, t)
