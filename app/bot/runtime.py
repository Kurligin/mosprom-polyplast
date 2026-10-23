"""Отправка сообщений вне обработчиков бота: планировщик, API Mini App, уведомления.

Контекст бота (очередь отправки) создаёт run_bot(). Если бот выключен (BOT_ENABLED=false),
функции пишут в лог и возвращают None — остальная логика работает.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_ctx = None


def set_ctx(ctx) -> None:
    global _ctx
    _ctx = ctx


def get_ctx():
    """BotCtx или None, если бот не запущен."""
    return _ctx


async def send(repo, text: str, *, employee=None, staff=None, chat_id: int | None = None,
               attachments: list[dict] | None = None, ticket_id: int | None = None) -> list[str] | None:
    """Новое сообщение новичку, сотруднику или в чат. Возвращает mid частей или None."""
    if _ctx is None:
        log.info("Бот не запущен, сообщение не отправлено")
        return None
    target_chat = chat_id
    user_id = None
    if target_chat is None:
        person = employee or staff
        target_chat = getattr(person, "chat_id", None)
        user_id = getattr(person, "max_user_id", None)
    return await _ctx.sender.send(repo, text, chat_id=target_chat, user_id=user_id, attachments=attachments,
                                  employee=employee, staff=staff, ticket_id=ticket_id)


async def edit(mid: str, text: str, attachments: list[dict] | None = None, *, chat_id: int | None = None) -> bool:
    """Изменить уже отправленное сообщение (PUT /messages). attachments=None — клавиатуру не трогать."""
    if _ctx is None:
        return False
    return await _ctx.sender.edit(mid, text, attachments=attachments, key=chat_id)


async def delete(mid: str, *, chat_id: int | None = None) -> bool:
    if _ctx is None:
        return False
    return await _ctx.sender.delete(mid, key=chat_id)


async def notify_hr_group(repo, text: str, attachments: list[dict] | None = None) -> list[str] | None:
    """Сообщение в HR-группу «МАКС» (chat_id сохраняется при добавлении бота в группу)."""
    chat_id = await repo.get_setting("hr_group_chat_id")
    if not chat_id:
        log.info("HR-группа не подключена, уведомление пропущено")
        return None
    try:
        return await send(repo, text, chat_id=int(chat_id), attachments=attachments)
    except Exception as e:  # noqa: BLE001 — уведомление не должно ломать основной сценарий
        log.warning("Не удалось уведомить HR-группу: %s", e)
        return None
