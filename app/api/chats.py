"""Чаты команды для Mini App: список групп с ботом, правила «кому нужен», HR-группа, «для представлений». Только HR."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.auth import HrDep
from app.api.deps import SessionDep
from app.api.views import iso
from app.bot import runtime
from app.chats import service
from app.db.models import TeamChat

router = APIRouter()


def chat_json(c: TeamChat, hr_id: int | None, stats: dict[int, dict[str, int]]) -> dict:
    st = stats.get(c.chat_id) or {}
    return {
        "id": c.chat_id,
        "title": service.title_of(c),
        "link": c.link,
        "audience": c.audience if c.audience in service.AUDIENCES else "none",
        "sites": list(c.sites or []),
        "work_type": c.work_type,
        "for_intro": bool(c.for_intro),
        "is_hr_group": c.chat_id == hr_id,
        "active": bool(c.active),
        "members_count": c.members_count,
        "added": st.get("added", 0),
        "links_sent": st.get("link", 0),
        "created_at": iso(c.created_at),
    }


def _order(c: TeamChat, hr_id: int | None) -> tuple:
    """HR-группа первой, дальше активные по названию, чаты без бота — в конце."""
    return (c.chat_id != hr_id, not c.active, service.title_of(c).lower(), c.chat_id)


@router.get("/chats")
async def list_chats(staff: HrDep, session: SessionDep) -> list:
    await service.ensure_hr_row(session)
    await session.commit()
    hr_id = await service.hr_group_id(session)
    stats = await service.chat_stats(session)
    rows = sorted((await session.scalars(select(TeamChat))).all(), key=lambda c: _order(c, hr_id))
    return [chat_json(c, hr_id, stats) for c in rows]


class ChatPatch(BaseModel):
    audience: Literal["none", "all", "sites"] | None = None
    sites: list[str] | None = Field(default=None, max_length=50)
    # null или "all" — и офису, и производству; поле не передано — не меняем
    work_type: Literal["office", "production", "all"] | None = None
    for_intro: bool | None = None
    is_hr_group: bool | None = None


@router.patch("/chats/{chat_id}")
async def patch_chat(chat_id: int, body: ChatPatch, staff: HrDep, session: SessionDep) -> dict:
    await service.ensure_hr_row(session)
    chat = await session.get(TeamChat, chat_id)
    if chat is None:
        raise HTTPException(404, "Чат не найден. Добавьте Корсика в группу «МАКС», и она появится здесь")
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if fields.get("work_type") == "all":
        fields["work_type"] = None
    if fields.get("is_hr_group") is True and not chat.active:
        raise HTTPException(409, "Бота удалили из этого чата: сначала добавьте его обратно")
    try:
        await service.update_chat(session, chat, fields=fields, sender=getattr(runtime.get_ctx(), "sender", None))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await session.commit()
    hr_id = await service.hr_group_id(session)
    return chat_json(chat, hr_id, await service.chat_stats(session))
