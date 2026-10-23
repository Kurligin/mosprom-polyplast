"""Сигналы в свободных сообщениях: если новичок пишет о себе про угрозы, травлю или опасность на работе,
сообщение сразу уходит HR красным флагом, а не в поиск по базе знаний.

Проверка идёт в любом состоянии диалога (/hr, рассказ о себе, окно ответа HR), кроме ожидания ответа
в опросе: там флаги проверяет сам опрос."""

from __future__ import annotations

import logging

from app.bot import hooks
from app.signals import rules, service

log = logging.getLogger(__name__)


@hooks.pre_route_always
async def red_flag_in_message(ctx, repo, emp, text: str, msg: dict) -> bool:
    if not text or getattr(repo, "s", None) is None:
        return False
    hits = [h for h in rules.detect(text, free_text=True) if h.level == "red"]
    if not hits:
        return False
    flags = await service.raise_hits(repo, emp, hits, text, source="message")
    notice = await service.employee_notice(repo, emp, flags)
    if notice:
        await ctx.sender.send(repo, notice, chat_id=emp.chat_id, user_id=emp.max_user_id, employee=emp)
    return True
