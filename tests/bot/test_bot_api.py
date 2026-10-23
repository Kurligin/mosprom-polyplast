"""Клиент Bot API «МАКС» (app/bot/api.py) без сети: httpx.MockTransport вместо сервера.

Проверяем, что клиент шлёт (метод, путь, параметры, тело) и как разбирает ошибки: от этого зависят
повторы при 429, пометка «бот остановлен» при 403 и откат на старую схему меню команд.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.bot import api as api_mod
from app.bot.api import UPDATE_TYPES, MaxApi, MaxApiError, make_ssl_context

pytestmark = pytest.mark.asyncio

BASE = "https://max.test"
ROOT = Path(__file__).resolve().parents[2]


class Server:
    """Фейковый сервер: ответы по (метод, путь), журнал запросов."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response | list[httpx.Response]] | None = None):
        self.routes = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        resp = self.routes.get(key)
        if isinstance(resp, list):
            resp = resp.pop(0)
        return resp if resp is not None else httpx.Response(200, json={"success": True})

    def body(self, idx: int = -1) -> dict:
        return json.loads(self.requests[idx].content)

    def params(self, idx: int = -1) -> dict:
        return dict(self.requests[idx].url.params)


def make_api(server: Server) -> MaxApi:
    client = httpx.AsyncClient(base_url=BASE, headers={"Authorization": "TOKEN"},
                               transport=httpx.MockTransport(server))
    return MaxApi("TOKEN", BASE, client=client)


async def _error(server: Server, call) -> MaxApiError:
    api = make_api(server)
    try:
        with pytest.raises(MaxApiError) as exc:
            await call(api)
        return exc.value
    finally:
        await api.close()


# --- разбор ошибок -------------------------------------------------------------------


async def test_400_with_json_code_and_message():
    server = Server({("POST", "/messages"): httpx.Response(
        400, json={"code": "attachment.not.ready", "message": "Key: errors.process.attachment.file.not.processed"})})
    e = await _error(server, lambda a: a.send_message("привет", chat_id=1))
    assert (e.status, e.code) == (400, "attachment.not.ready")
    assert e.message == "Key: errors.process.attachment.file.not.processed"
    assert not e.is_forbidden and not e.is_rate_limited and e.retry_after is None
    assert "400 attachment.not.ready" in str(e)


async def test_error_code_from_error_field():
    server = Server({("GET", "/me"): httpx.Response(401, json={"error": "verify.token", "message": "Invalid token"})})
    e = await _error(server, lambda a: a.get_me())
    assert (e.status, e.code, e.message) == (401, "verify.token", "Invalid token")


async def test_403_chat_denied_is_forbidden():
    server = Server({("POST", "/messages"): httpx.Response(
        403, json={"code": "chat.denied", "message": "dialog.suspended"})})
    e = await _error(server, lambda a: a.send_message("привет", user_id=5))
    assert e.is_forbidden and e.code == "chat.denied"


async def test_denied_code_without_403_is_forbidden():
    """Остановленный бот иногда отвечает 400 с кодом *.denied: это тоже «писать нельзя»."""
    server = Server({("POST", "/messages"): httpx.Response(400, json={"code": "chat.denied"})})
    e = await _error(server, lambda a: a.send_message("привет", user_id=5))
    assert e.status == 400 and e.is_forbidden


async def test_429_with_retry_after():
    server = Server({("POST", "/messages"): httpx.Response(
        429, json={"code": "too.many.requests"}, headers={"Retry-After": "7"})})
    e = await _error(server, lambda a: a.send_message("x", chat_id=1))
    assert e.is_rate_limited and e.retry_after == 7.0


async def test_429_with_http_date_retry_after_is_ignored():
    server = Server({("POST", "/messages"): httpx.Response(
        429, text="slow down", headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})})
    e = await _error(server, lambda a: a.send_message("x", chat_id=1))
    assert e.is_rate_limited and e.retry_after is None


async def test_5xx_not_json_keeps_raw_text():
    page = "<html><body>502 Bad Gateway</body></html>" + "x" * 500
    server = Server({("GET", "/updates"): httpx.Response(502, text=page)})
    e = await _error(server, lambda a: a.get_updates())
    assert e.status == 502 and e.code == ""
    assert e.message == page[:300]
    assert not e.is_forbidden and not e.is_rate_limited


@pytest.mark.parametrize("payload", [["bad", "gateway"], "Service Unavailable", 503, None])
async def test_error_with_json_that_is_not_object(payload):
    """Прокси может вернуть JSON-строку или массив: всё равно MaxApiError, а не AttributeError."""
    server = Server({("GET", "/updates"): httpx.Response(503, content=json.dumps(payload).encode(),
                                                         headers={"content-type": "application/json"})})
    e = await _error(server, lambda a: a.get_updates())
    assert e.status == 503 and e.code == ""
    assert e.message == json.dumps(payload)


async def test_empty_success_body_is_empty_dict():
    server = Server({("DELETE", "/messages"): httpx.Response(200, content=b"")})
    api = make_api(server)
    assert await api.delete_message("mid.1") == {}
    await api.close()


# --- меню команд -------------------------------------------------------------------------


COMMANDS = [{"name": "hr", "description": "Написать HR"}]


@pytest.mark.parametrize("status", [404, 405])
async def test_set_commands_falls_back_to_patch_me(status):
    server = Server({("PATCH", "/me/commands"): httpx.Response(status, json={"code": "not.found"}),
                     ("PATCH", "/me"): httpx.Response(200, json={"name": "Корсик"})})
    api = make_api(server)
    assert await api.set_commands(COMMANDS) == {"name": "Корсик"}
    assert [(r.method, r.url.path) for r in server.requests] == [("PATCH", "/me/commands"), ("PATCH", "/me")]
    assert server.body(0) == server.body(1) == {"commands": COMMANDS}
    await api.close()


async def test_set_commands_new_schema_no_fallback():
    server = Server()
    api = make_api(server)
    await api.set_commands(COMMANDS)
    assert [r.url.path for r in server.requests] == ["/me/commands"]
    await api.close()


async def test_set_commands_other_error_is_raised():
    server = Server({("PATCH", "/me/commands"): httpx.Response(500, text="boom")})
    e = await _error(server, lambda a: a.set_commands(COMMANDS))
    assert e.status == 500
    assert [r.url.path for r in server.requests] == ["/me/commands"], "без отката на PATCH /me"


# --- подписки и обновления ----------------------------------------------------------------------


async def test_subscriptions_list_and_delete():
    server = Server({("GET", "/subscriptions"): [
        httpx.Response(200, json={"subscriptions": [{"url": "https://hook.test/a", "time": 1}]}),
        httpx.Response(200, json={}),
    ]})
    api = make_api(server)
    assert await api.get_subscriptions() == [{"url": "https://hook.test/a", "time": 1}]
    assert await api.get_subscriptions() == []
    await api.delete_subscription("https://hook.test/a")
    req = server.requests[-1]
    assert (req.method, req.url.path, server.params()) == ("DELETE", "/subscriptions", {"url": "https://hook.test/a"})
    await api.close()


async def test_get_updates_params():
    server = Server({("GET", "/updates"): httpx.Response(200, json={"updates": [], "marker": 42})})
    api = make_api(server)
    assert await api.get_updates(marker=41, timeout=30, limit=50, types=UPDATE_TYPES) == {"updates": [], "marker": 42}
    assert server.params() == {"marker": "41", "timeout": "30", "limit": "50", "types": ",".join(UPDATE_TYPES)}
    await api.get_updates()
    assert server.params() == {"timeout": "30", "limit": "100"}, "пустые marker и types не уходят"
    await api.close()


# --- сообщения -----------------------------------------------------------------------------


async def test_send_message_to_chat():
    server = Server({("POST", "/messages"): httpx.Response(200, json={"message": {"body": {"mid": "mid.1"}}})})
    api = make_api(server)
    kb = [{"type": "inline_keyboard", "payload": {"buttons": [[{"type": "callback", "text": "Да", "payload": "x"}]]}}]
    r = await api.send_message("**Привет**", chat_id=-100, attachments=kb, notify=False)
    assert r["message"]["body"]["mid"] == "mid.1"
    assert server.params() == {"chat_id": "-100"}
    assert server.body() == {"text": "**Привет**", "notify": False, "attachments": kb, "format": "markdown"}
    assert server.requests[-1].headers["Authorization"] == "TOKEN"
    await api.close()


async def test_send_message_to_user_without_format():
    server = Server()
    api = make_api(server)
    await api.send_message("Чужие слова *как есть*", user_id=777, fmt=None, attachments=[])
    assert server.params() == {"user_id": "777"}
    assert server.body() == {"text": "Чужие слова *как есть*", "notify": True}, "без format и пустых вложений"
    await api.close()


async def test_answer_callback():
    server = Server()
    api = make_api(server)
    await api.answer_callback("cb-1", message={"text": "Готово"}, notification="Записал")
    req = server.requests[-1]
    assert (req.method, req.url.path, server.params()) == ("POST", "/answers", {"callback_id": "cb-1"})
    assert server.body() == {"message": {"text": "Готово"}, "notification": "Записал"}
    await api.answer_callback("cb-2")
    assert server.body() == {}
    await api.close()


async def test_edit_message_attachments_none_vs_empty():
    server = Server()
    api = make_api(server)
    await api.edit_message("mid.5", "новый текст")
    req = server.requests[-1]
    assert (req.method, req.url.path, server.params()) == ("PUT", "/messages", {"message_id": "mid.5"})
    assert server.body() == {"text": "новый текст", "format": "markdown"}, "None — вложения не трогаем"
    await api.edit_message("mid.5", "без кнопок", attachments=[], fmt=None)
    assert server.body() == {"text": "без кнопок", "attachments": []}, "[] — убрать кнопки"
    await api.close()


async def test_delete_message():
    server = Server()
    api = make_api(server)
    await api.delete_message("mid.9")
    req = server.requests[-1]
    assert (req.method, req.url.path, server.params()) == ("DELETE", "/messages", {"message_id": "mid.9"})
    await api.close()


# --- загрузка файлов -----------------------------------------------------------------------------


UPLOAD_URL = "https://upload.test/upload?sig=abc"


def _upload_server(info: dict, uploaded: httpx.Response) -> Server:
    server = Server({("POST", "/uploads"): httpx.Response(200, json=info)})
    real_call = server.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "upload.test":
            server.requests.append(request)
            return uploaded
        return real_call(request)

    server.handler = handler  # type: ignore[attr-defined]
    return server


def _upload_api(server: Server) -> MaxApi:
    client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(server.handler))  # type: ignore[attr-defined]
    return MaxApi("TOKEN", BASE, client=client)


async def test_upload_image_two_steps():
    photos = {"photos": {"p1": {"token": "img-token"}}}
    server = _upload_server({"url": UPLOAD_URL}, httpx.Response(200, json=photos))
    api = _upload_api(server)
    assert await api.upload("image", "photo.png", b"\x89PNG-data", "image/png") == photos
    first, second = server.requests
    assert (first.method, first.url.path, dict(first.url.params)) == ("POST", "/uploads", {"type": "image"})
    assert str(second.url) == UPLOAD_URL
    body = second.content
    assert b'name="data"; filename="photo.png"' in body and b"\x89PNG-data" in body
    assert b"Content-Type: image/png" in body
    await api.close()


async def test_upload_file_token_from_first_step():
    server = _upload_server({"url": UPLOAD_URL, "token": "file-token"},
                            httpx.Response(200, text="<retval>1</retval>"))
    api = _upload_api(server)
    assert await api.upload("file", "plan.pdf", b"%PDF", "application/pdf") == {"token": "file-token"}
    await api.close()


async def test_upload_file_token_from_upload_server():
    server = _upload_server({"url": UPLOAD_URL}, httpx.Response(200, json={"fileId": 9, "token": "late-token"}))
    api = _upload_api(server)
    assert await api.upload("file", "plan.pdf", b"%PDF", "application/pdf") == {"token": "late-token"}
    await api.close()


async def test_upload_image_not_json_is_empty():
    server = _upload_server({"url": UPLOAD_URL}, httpx.Response(200, text="ok"))
    api = _upload_api(server)
    assert await api.upload("image", "a.png", b"x", "image/png") == {}, "токен проверит app.bot.uploads"
    await api.close()


async def test_upload_without_url():
    server = _upload_server({"token": "t"}, httpx.Response(200))
    api = _upload_api(server)
    with pytest.raises(MaxApiError) as exc:
        await api.upload("file", "a.pdf", b"x", "application/pdf")
    assert (exc.value.status, exc.value.code) == (500, "upload.no_url")
    assert len(server.requests) == 1, "без url файл никуда не уходит"
    await api.close()


async def test_upload_failed():
    server = _upload_server({"url": UPLOAD_URL}, httpx.Response(413, text="Request Entity Too Large"))
    api = _upload_api(server)
    with pytest.raises(MaxApiError) as exc:
        await api.upload("file", "big.pdf", b"x", "application/pdf")
    assert (exc.value.status, exc.value.code, exc.value.message) == (413, "upload.failed", "Request Entity Too Large")
    await api.close()


# --- действия и групповые чаты ------------------------------------------------------------------


async def test_send_action_get_chat_add_members():
    server = Server({("GET", "/chats/-70"): httpx.Response(200, json={"chat_id": -70, "title": "Команда (синтетика)",
                                                                      "link": "https://max.ru/join/x"}),
                     ("POST", "/chats/-70/members"): httpx.Response(200, json={"success": True,
                                                                               "failed_user_ids": []})})
    api = make_api(server)
    await api.send_action(-70)
    assert (server.requests[-1].url.path, server.body()) == ("/chats/-70/actions", {"action": "typing_on"})
    await api.send_action(-70, "sending_photo")
    assert server.body() == {"action": "sending_photo"}
    chat = await api.get_chat(-70)
    assert chat["title"] == "Команда (синтетика)" and server.requests[-1].method == "GET"
    r = await api.add_members(-70, (101, 102))
    assert r["success"] and server.body() == {"user_ids": [101, 102]}
    await api.close()


async def test_add_members_forbidden_raises():
    server = Server({("POST", "/chats/-70/members"): httpx.Response(403, json={"code": "chat.denied"})})
    e = await _error(server, lambda a: a.add_members(-70, [1]))
    assert e.is_forbidden


# --- клиент по умолчанию и SSL ------------------------------------------------------------------


async def test_default_client_settings():
    api = MaxApi("SECRET", "https://platform-api.max.ru/", ca_bundle_file=None)
    try:
        c = api._client
        assert str(c.base_url) == "https://platform-api.max.ru"
        assert c.headers["Authorization"] == "SECRET"
        assert c.timeout.read == 60.0 and c.timeout.connect == 15.0, "read длиннее long polling (30 с)"
    finally:
        await api.close()


class _Ctx:
    def __init__(self):
        self.loaded: list[str] = []

    def load_verify_locations(self, path):
        self.loaded.append(path)


@pytest.fixture
def fake_ctx(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(api_mod.ssl, "create_default_context", lambda cafile=None: ctx)
    return ctx


async def test_ssl_context_loads_existing_bundle(fake_ctx, tmp_path):
    pem = tmp_path / "ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\n")
    assert make_ssl_context(str(pem)) is fake_ctx
    assert fake_ctx.loaded == [str(pem)]


async def test_ssl_context_relative_path_from_code_dir(fake_ctx, monkeypatch, tmp_path):
    """Относительный путь из .env ищем от /code, если процесс запущен не из корня проекта."""
    monkeypatch.chdir(tmp_path)
    real_exists = api_mod.os.path.exists
    monkeypatch.setattr(api_mod.os.path, "exists", lambda p: p == "/code/certs/ca.pem" or real_exists(p))
    make_ssl_context("certs/ca.pem")
    assert fake_ctx.loaded == ["/code/certs/ca.pem"]


async def test_ssl_context_missing_bundle_warns(fake_ctx, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="app.bot.api"):
        make_ssl_context(str(tmp_path / "nope.pem"))
    assert fake_ctx.loaded == []
    assert "не найден" in caplog.text


async def test_ssl_context_without_bundle(fake_ctx):
    make_ssl_context(None)
    make_ssl_context("")
    assert fake_ctx.loaded == []


async def test_real_russian_ca_is_trusted():
    """Корневой сертификат НУЦ Минцифры из репозитория добавляется к certifi."""
    base = make_ssl_context(None).cert_store_stats()["x509_ca"]
    with_nuc = make_ssl_context(str(ROOT / "certs" / "russian_trusted_ca_bundle.pem")).cert_store_stats()["x509_ca"]
    assert with_nuc > base
