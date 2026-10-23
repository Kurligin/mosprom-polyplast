"""Вложения «МАКС»: файл → готовый элемент `attachments` для отправки сообщения.

Схема (github.com/max-messenger/api-schema, dev.max.ru/docs-api):
- POST /uploads?type=image|file → url; файл уходит на url полем `data` (это делает MaxApi.upload).
- Ответ сервера загрузки: image → {"photos": {"<id>": {"token": …}}}, file → {"fileId": …, "token": …}.
- В сообщении: {"type": "image", "payload": {"token": …}}, {"type": "file", "payload": {"token": …}}.
- Файл (`file`) отправляется только с клавиатурой, без картинок в том же сообщении.
- Пока сервер обрабатывает файл, POST /messages отвечает 400 `attachment.not.ready` — повторяем с паузой.

Токен переиспользуется: кэш в `Setting` по sha256 содержимого файла, повторно файл не загружается.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
from pathlib import Path

from app.bot.api import MaxApiError

log = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CACHE_PREFIX = "upload:"            # + 48 символов sha256 — укладываемся в Setting.key (64)
NOT_READY_PAUSES = (1.0, 2.0, 3.0, 5.0)


def kind_for(path: str | Path) -> str:
    return "image" if Path(path).suffix.lower() in IMAGE_EXT else "file"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token(kind: str, uploaded: dict) -> str | None:
    """Токен из ответа загрузки: у image — внутри photos, у file — сразу."""
    if not uploaded:
        return None
    if uploaded.get("token"):
        return uploaded["token"]
    photos = uploaded.get("photos") or {}
    for item in photos.values():
        if isinstance(item, dict) and item.get("token"):
            return item["token"]
    return None


def is_not_ready(e: Exception) -> bool:
    return isinstance(e, MaxApiError) and "not.ready" in f"{e.code} {e.message}".lower()


async def _cache_get(repo, key: str):
    if repo is not None:
        return await repo.get_setting(key)
    from app.bot.repo import SqlRepo
    from app.db.session import async_session

    async with async_session() as s:
        return await SqlRepo(s).get_setting(key)


async def _cache_set(repo, key: str, value) -> None:
    if repo is not None:
        await repo.set_setting(key, value)
        return
    from app.bot.repo import SqlRepo
    from app.db.session import async_session

    async with async_session() as s:
        await SqlRepo(s).set_setting(key, value)
        await s.commit()


def _sender():
    from app.bot import runtime

    ctx = runtime.get_ctx()
    return getattr(ctx, "sender", None)


async def attachment(path: str | Path, kind: str | None = None, *, repo=None, sender=None) -> dict:
    """Готовый элемент `attachments`: {"type": "image"|"file", "payload": {"token": …}}.

    repo — SqlRepo обработчика (кэш пишется в его сессию) или None (своя короткая сессия).
    sender — app.bot.sender.Sender; по умолчанию — из контекста запущенного бота.
    RuntimeError — бот не запущен; MaxApiError — ошибка загрузки.
    """
    p = Path(path)
    kind = kind or kind_for(p)
    data = p.read_bytes()
    key = CACHE_PREFIX + _sha256(data)[:48]
    cached = await _cache_get(repo, key)
    if isinstance(cached, dict) and cached.get("token") and cached.get("kind") == kind:
        return {"type": kind, "payload": {"token": cached["token"]}}

    sender = sender or _sender()
    if sender is None:
        raise RuntimeError("Бот не запущен: файл не загрузить")
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    uploaded = await sender.call(None, sender.api.upload, kind, p.name, data, ctype)
    token = _token(kind, uploaded)
    if not token:
        raise MaxApiError(500, "upload.no_token", str(uploaded)[:200])
    await _cache_set(repo, key, {"kind": kind, "token": token, "name": p.name})
    log.info("Загружен %s (%s)", p.name, kind)
    return {"type": kind, "payload": {"token": token}}


async def send(sender, repo, text: str, *, employee=None, attachments: list[dict] | None = None,
               sleep=asyncio.sleep) -> list[str] | None:
    """Отправить новичку сообщение с вложениями; «attachment.not.ready» → повтор с паузой."""
    pauses = list(NOT_READY_PAUSES)
    while True:
        try:
            return await sender.send(repo, text, chat_id=getattr(employee, "chat_id", None),
                                     user_id=getattr(employee, "max_user_id", None),
                                     attachments=attachments, employee=employee)
        except MaxApiError as e:
            if not is_not_ready(e) or not pauses:
                raise
            pause = pauses.pop(0)
            log.info("Вложение ещё обрабатывается, повтор через %.0f с", pause)
            await sleep(pause)
