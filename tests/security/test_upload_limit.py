"""Лимит загрузки: тело запроса обрывается по Content-Length и по фактическому размеру, до разбора формы.

Без ограничителя Starlette сначала целиком пишет файл во временный файл, и только потом save_upload видит размер.
"""

from typing import Annotated

import pytest
from fastapi import FastAPI, File, UploadFile

from app.api.deps import MAX_BODY, MAX_UPLOAD, BodySizeLimit

pytestmark = pytest.mark.asyncio

MB = 1024 * 1024
BOUNDARY = b"korsikboundary"


def _app(limit: int | None = None):
    app = FastAPI()
    calls = []

    @app.post("/api/kb/documents")
    async def upload(file: Annotated[UploadFile, File()]):
        calls.append(file.filename)
        return {"ok": True}

    if limit is None:
        app.add_middleware(BodySizeLimit)
    else:
        app.add_middleware(BodySizeLimit, max_body=limit)
    return app, calls


def _scope(headers: dict) -> dict:
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/kb/documents", "raw_path": b"/api/kb/documents", "root_path": "",
            "query_string": b"", "server": ("test", 80), "client": ("127.0.0.1", 1),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}


def _multipart_chunks(total: int, chunk: int = MB):
    head = (b"--" + BOUNDARY + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.pdf\"\r\n"
            b"Content-Type: application/pdf\r\n\r\n")
    yield head
    block = b"a" * chunk
    sent = 0
    while sent < total:
        yield block
        sent += len(block)
    yield b"\r\n--" + BOUNDARY + b"--\r\n"


async def _run(app, headers: dict, chunks):
    pulled = {"bytes": 0, "messages": 0}
    it = iter(chunks)
    out = []

    async def receive():
        try:
            body = next(it)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        pulled["bytes"] += len(body)
        pulled["messages"] += 1
        return {"type": "http.request", "body": body, "more_body": True}

    async def send(message):
        out.append(message)

    await app(_scope(headers), receive, send)
    status = next(m["status"] for m in out if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")
    return status, body, pulled


async def test_limits():
    assert MAX_UPLOAD == 50 * MB
    assert MAX_UPLOAD < MAX_BODY <= MAX_UPLOAD + 2 * MB  # запас на заголовки multipart


async def test_content_length_over_limit_rejected_without_reading():
    app, calls = _app()
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY.decode()}",
               "content-length": str(5 * 1024 * MB)}
    status, body, pulled = await _run(app, headers, _multipart_chunks(5 * 1024 * MB))
    assert status == 413
    assert "50 МБ" in body.decode()
    assert pulled["bytes"] == 0 and calls == []


async def test_bad_content_length_rejected():
    app, calls = _app()
    status, _, pulled = await _run(app, {"content-length": "много"}, [])
    assert status == 400 and calls == [] and pulled["bytes"] == 0


async def test_streamed_body_cut_at_limit():
    """Без Content-Length (chunked): читаем, пока не превысили лимит, дальше — 413, хвост не читаем."""
    limit = 3 * MB
    app, calls = _app(limit)
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY.decode()}"}
    status, body, pulled = await _run(app, headers, _multipart_chunks(40 * MB))
    assert status == 413, body
    assert calls == []
    assert pulled["bytes"] <= limit + 2 * MB  # остановились сразу после лимита, а не на 40 МБ


async def test_lying_content_length_cut_at_limit():
    """Content-Length занижен — режем по фактическому размеру."""
    limit = 3 * MB
    app, calls = _app(limit)
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY.decode()}", "content-length": "1000"}
    status, _, pulled = await _run(app, headers, _multipart_chunks(40 * MB))
    assert status == 413 and calls == []
    assert pulled["bytes"] <= limit + 2 * MB


async def test_small_upload_passes():
    app, calls = _app(3 * MB)
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY.decode()}"}
    status, body, _ = await _run(app, headers, _multipart_chunks(MB, chunk=256 * 1024))
    assert status == 200, body
    assert calls == ["big.pdf"]


async def test_main_app_has_body_limit():
    """Ограничитель подключён к приложению (app/main.py)."""
    from app.main import app

    assert any(m.cls is BodySizeLimit for m in app.user_middleware), "app.add_middleware(BodySizeLimit) в app/main.py"
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY.decode()}",
               "content-length": str(MAX_BODY + 1)}
    status, _, pulled = await _run(app, headers, _multipart_chunks(MAX_BODY + 1))
    assert status == 413 and pulled["bytes"] == 0
