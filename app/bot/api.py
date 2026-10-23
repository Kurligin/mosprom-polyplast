"""Тонкий клиент Bot API «МАКС» на httpx.

Свой клиент вместо maxapi: полный контроль над base_url, SSL (сертификат НУЦ Минцифры) и лимитами.
Схема API: github.com/max-messenger/api-schema (schema.yaml).
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import Any

import certifi
import httpx

log = logging.getLogger(__name__)

# Типы событий, которые бот получает через long polling
UPDATE_TYPES = [
    "bot_started",
    "message_created",
    "message_callback",
    "bot_stopped",
    "bot_added",
    "bot_removed",
]


class MaxApiError(Exception):
    """Ошибка ответа Bot API: HTTP-код + строковый код ошибки из тела."""

    def __init__(self, status: int, code: str = "", message: str = "", retry_after: float | None = None):
        super().__init__(f"{status} {code} {message}".strip())
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after

    @property
    def is_forbidden(self) -> bool:
        # 403 chat.denied — пользователь остановил бота или запретил писать
        return self.status == 403 or "denied" in (self.code or "")

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429


def make_ssl_context(ca_bundle_file: str | None) -> ssl.SSLContext:
    """certifi + сертификаты НУЦ Минцифры: platform-api2.max.ru подписан российским CA."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    if ca_bundle_file:
        path = ca_bundle_file
        if not os.path.isabs(path) and not os.path.exists(path):
            path = os.path.join("/code", path)
        if os.path.exists(path):
            ctx.load_verify_locations(path)
        else:
            log.warning("Файл сертификатов НУЦ не найден: %s", ca_bundle_file)
    return ctx


class MaxApi:
    def __init__(self, token: str, base_url: str, ca_bundle_file: str | None = None,
                 client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": token},
            verify=make_ssl_context(ca_bundle_file),
            # read больше таймаута long polling
            timeout=httpx.Timeout(15.0, read=60.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: Any = None) -> dict:
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        resp = await self._client.request(method, path, params=params, json=json)
        if resp.status_code >= 400:
            code, message = "", resp.text[:300]
            try:
                body = resp.json()
                code = str(body.get("code") or body.get("error") or "")
                message = str(body.get("message") or "")
            except ValueError:
                pass  # тело не JSON — оставляем сырой текст
            retry_after = None
            if resp.headers.get("Retry-After"):
                try:
                    retry_after = float(resp.headers["Retry-After"])
                except ValueError:
                    pass
            raise MaxApiError(resp.status_code, code, message, retry_after)
        if not resp.content:
            return {}
        return resp.json()

    # --- бот ---

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def set_commands(self, commands: list[dict]) -> dict:
        """Меню команд. В актуальной схеме — PATCH /me/commands, в старой — PATCH /me."""
        try:
            return await self._request("PATCH", "/me/commands", json={"commands": commands})
        except MaxApiError as e:
            if e.status not in (404, 405):
                raise
            return await self._request("PATCH", "/me", json={"commands": commands})

    # --- подписки и обновления ---

    async def get_subscriptions(self) -> list[dict]:
        data = await self._request("GET", "/subscriptions")
        return data.get("subscriptions") or []

    async def delete_subscription(self, url: str) -> dict:
        return await self._request("DELETE", "/subscriptions", params={"url": url})

    async def get_updates(self, marker: int | None = None, timeout: int = 30, limit: int = 100,
                          types: list[str] | None = None) -> dict:
        params = {"marker": marker, "timeout": timeout, "limit": limit,
                  "types": ",".join(types) if types else None}
        return await self._request("GET", "/updates", params=params)

    # --- сообщения ---

    async def send_message(self, text: str, *, chat_id: int | None = None, user_id: int | None = None,
                           attachments: list[dict] | None = None, fmt: str | None = "markdown",
                           notify: bool = True) -> dict:
        body: dict[str, Any] = {"text": text, "notify": notify}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        params = {"chat_id": chat_id} if chat_id is not None else {"user_id": user_id}
        return await self._request("POST", "/messages", params=params, json=body)

    async def answer_callback(self, callback_id: str, *, message: dict | None = None,
                              notification: str | None = None) -> dict:
        body: dict[str, Any] = {}
        if message is not None:
            body["message"] = message
        if notification:
            body["notification"] = notification
        return await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def edit_message(self, mid: str, text: str, *, attachments: list[dict] | None = None,
                           fmt: str | None = "markdown") -> dict:
        """PUT /messages: изменить отправленное сообщение. attachments=None — вложения не трогать, [] — убрать."""
        body: dict[str, Any] = {"text": text}
        if attachments is not None:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("PUT", "/messages", params={"message_id": mid}, json=body)

    async def delete_message(self, mid: str) -> dict:
        return await self._request("DELETE", "/messages", params={"message_id": mid})

    async def upload(self, kind: str, filename: str, data: bytes, content_type: str) -> dict:
        """POST /uploads?type=image|file|video|audio → url для загрузки → загрузка файла.

        Возвращает payload вложения: для image — ответ сервера загрузки (photos/token),
        для file/video/audio — {"token": ...} из первого шага.
        """
        info = await self._request("POST", "/uploads", params={"type": kind})
        url = info.get("url")
        if not url:
            raise MaxApiError(500, "upload.no_url", str(info)[:200])
        resp = await self._client.post(url, files={"data": (filename, data, content_type)},
                                       timeout=httpx.Timeout(60.0))
        if resp.status_code >= 400:
            raise MaxApiError(resp.status_code, "upload.failed", resp.text[:300])
        try:
            uploaded = resp.json()
        except ValueError:
            uploaded = {}
        if kind == "image":
            return uploaded
        return {"token": info.get("token") or uploaded.get("token")}

    async def send_action(self, chat_id: int, action: str = "typing_on") -> dict:
        """POST /chats/{chatId}/actions: «печатает…» (typing_on), «отправляет фото» и т. п."""
        return await self._request("POST", f"/chats/{chat_id}/actions", json={"action": action})

    # --- групповые чаты ---

    async def get_chat(self, chat_id: int) -> dict:
        """GET /chats/{chatId}: название (title), ссылка-приглашение (link), participants_count, status."""
        return await self._request("GET", f"/chats/{chat_id}")

    async def add_members(self, chat_id: int, user_ids: list[int]) -> dict:
        """POST /chats/{chatId}/members (addMembers). Бот должен быть админом чата.

        Ответ — ModifyMembersResult: success, failed_user_ids, failed_user_details[{error_code, user_ids}]
        (add.participant.privacy — не пускают настройки приватности, add.participant.not.found).
        """
        return await self._request("POST", f"/chats/{chat_id}/members", json={"user_ids": list(user_ids)})

