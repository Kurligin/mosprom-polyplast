"""Очередь отправки: лимиты «МАКС», повторы на 429, 403 → «остановил бота», нарезка длинных текстов, журнал."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

from app.bot.api import MaxApiError

log = logging.getLogger(__name__)

MAX_TEXT = 3500          # длиннее — режем на части
PER_CHAT_RATE = 2        # сообщений в секунду на чат
GLOBAL_RATE = 30         # запросов в секунду на бота
MAX_RETRIES = 5


class RateLimiter:
    """Скользящее окно: не больше `rate` событий за `per` секунд на ключ."""

    def __init__(self, rate: int, per: float = 1.0):
        self.rate = rate
        self.per = per
        self._hits: dict[Any, deque] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: Any = None) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                hits = self._hits.setdefault(key, deque())
                while hits and now - hits[0] >= self.per:
                    hits.popleft()
                if len(hits) < self.rate:
                    hits.append(now)
                    return
                wait = self.per - (now - hits[0])
            await asyncio.sleep(max(wait, 0.01))


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Режем по абзацам, затем по строкам, в крайнем случае — жёстко по длине."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


class Sender:
    def __init__(self, api, per_chat_rate: int = PER_CHAT_RATE, global_rate: int = GLOBAL_RATE,
                 sleep=asyncio.sleep):
        self.api = api
        self.chat_limiter = RateLimiter(per_chat_rate)
        self.global_limiter = RateLimiter(global_rate)
        self._sleep = sleep

    async def call(self, key: Any, fn, *args, **kwargs):
        """Любой запрос к API через лимиты и повторы на 429 / сетевые ошибки."""
        for attempt in range(1, MAX_RETRIES + 1):
            if key is not None:
                await self.chat_limiter.acquire(key)
            await self.global_limiter.acquire(None)
            try:
                return await fn(*args, **kwargs)
            except MaxApiError as e:
                if e.is_rate_limited and attempt < MAX_RETRIES:
                    pause = e.retry_after or attempt
                    log.warning("429 от «МАКС», пауза %.1f с", pause)
                    await self._sleep(pause)
                    continue
                if e.status >= 500 and attempt < MAX_RETRIES:
                    await self._sleep(attempt)
                    continue
                raise
            except httpx.TransportError as e:
                if attempt < MAX_RETRIES:
                    log.warning("Сеть: %s, повтор", e)
                    await self._sleep(attempt)
                    continue
                raise

    async def send(self, repo, text: str, *, chat_id: int | None = None, user_id: int | None = None,
                   attachments: list[dict] | None = None, employee=None, staff=None,
                   ticket_id: int | None = None, fmt: str | None = "markdown") -> list[str] | None:
        """Отправить текст (с нарезкой). Клавиатура — у последней части.

        Возвращает список mid или None, если пользователь запретил сообщения (403).
        """
        if chat_id is None and user_id is None:
            chat_id = getattr(employee, "chat_id", None) or getattr(staff, "chat_id", None)
            user_id = getattr(employee, "max_user_id", None) or getattr(staff, "max_user_id", None)
        key = chat_id if chat_id is not None else ("u", user_id)
        parts = split_text(text)
        mids: list[str] = []
        for i, part in enumerate(parts):
            atts = attachments if i == len(parts) - 1 else None
            try:
                resp = await self.call(key, self.api.send_message, part, chat_id=chat_id, user_id=user_id,
                                       attachments=atts, fmt=fmt)
            except MaxApiError as e:
                if e.is_forbidden:
                    log.info("403 при отправке в чат %s — помечаем «остановил бота»", key)
                    if employee is not None:
                        employee.status = "stopped"
                    return None
                raise
            msg = (resp or {}).get("message") or {}
            mid = (msg.get("body") or {}).get("mid")
            real_chat = (msg.get("recipient") or {}).get("chat_id") or chat_id
            mids.append(mid)
            if repo is not None:
                await repo.add_message(
                    employee_id=getattr(employee, "id", None),
                    staff_id=getattr(staff, "id", None),
                    chat_id=real_chat,
                    direction="out",
                    text=part,
                    mid=mid,
                    ticket_id=ticket_id,
                )
        return mids

    async def answer(self, callback_id: str, *, message: dict | None = None,
                     notification: str | None = None, key: Any = None):
        try:
            return await self.call(key, self.api.answer_callback, callback_id,
                                   message=message, notification=notification)
        except MaxApiError as e:
            log.warning("Не удалось ответить на callback: %s", e)
            return None

    async def edit(self, mid: str, text: str, *, attachments: list[dict] | None = None, key: Any = None) -> bool:
        """Изменить отправленное сообщение. False — не получилось (сообщение удалено, старое и т. п.)."""
        try:
            await self.call(key, self.api.edit_message, mid, text, attachments=attachments)
            return True
        except MaxApiError as e:
            log.warning("Не удалось изменить сообщение %s: %s", mid, e)
            return False

    async def delete(self, mid: str, *, key: Any = None) -> bool:
        try:
            await self.call(key, self.api.delete_message, mid)
            return True
        except MaxApiError as e:
            log.warning("Не удалось удалить сообщение %s: %s", mid, e)
            return False

