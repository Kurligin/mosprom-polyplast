"""Общие зависимости API (app/api/deps.py): модули других команд по требованию, ограничитель тела запроса."""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from app.api import deps
from app.api.deps import BodySizeLimit

pytestmark = pytest.mark.asyncio


@pytest.fixture
def modules(tmp_path, monkeypatch):
    """Временные модули: рабочий, сломанный и с отсутствующей зависимостью."""
    (tmp_path / "korsik_test_ok.py").write_text("def fn():\n    return 'ok'\n", encoding="utf-8")
    (tmp_path / "korsik_test_broken.py").write_text("raise RuntimeError('ошибка при импорте')\n", encoding="utf-8")
    (tmp_path / "korsik_test_needs_dep.py").write_text("import korsik_missing_dependency\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield
    import sys

    for name in ("korsik_test_ok", "korsik_test_broken", "korsik_test_needs_dep"):
        sys.modules.pop(name, None)


async def test_optional_module_states(modules, caplog):
    with caplog.at_level(logging.WARNING, logger="app.api.deps"):
        assert deps.optional("korsik_test_ok", "fn")() == "ok"
        assert deps.optional("korsik_test_ok", "nope") is None
        assert deps.optional("korsik_test_absent", "fn") is None
        assert caplog.text == "", "модуля ещё нет — молча"
        assert deps.optional("korsik_test_broken", "fn") is None
        assert deps.optional("korsik_test_needs_dep", "fn") is None
    assert caplog.text.count("не загрузился") == 2, "сломанный модуль и модуль без зависимости — в лог"


async def test_need_raises_501_with_name():
    with pytest.raises(HTTPException) as exc:
        deps.need("korsik_test_absent", "fn", "Модуль X")
    assert exc.value.status_code == 501 and exc.value.detail == "Модуль X ещё не готов (korsik_test_absent.fn)"


async def test_call_sync_and_async():
    async def a(x):
        return x * 2

    assert await deps.call(lambda x: x + 1, 1) == 2
    assert await deps.call(a, 2) == 4


# --- BodySizeLimit: не-HTTP и ошибки приложения ---------------------------------------------------


async def test_body_limit_passes_non_http_scopes():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    mw = BodySizeLimit(app, max_body=10)
    await mw({"type": "lifespan"}, None, None)
    await mw({"type": "websocket", "headers": [(b"content-length", b"999")]}, None, None)
    assert seen == ["lifespan", "websocket"]


async def test_body_limit_reraises_app_errors_within_limit():
    async def app(scope, receive, send):
        await receive()
        raise RuntimeError("ошибка обработчика")

    async def receive():
        return {"type": "http.request", "body": b"abc", "more_body": False}

    mw = BodySizeLimit(app, max_body=10)
    with pytest.raises(RuntimeError, match="ошибка обработчика"):
        await mw({"type": "http", "headers": []}, receive, None)


async def test_body_limit_stops_reading_after_limit():
    """Приложение поймало обрыв и читает дальше — тело больше не отдаём, ответ 413."""
    reads, out = [], []

    async def app(scope, receive, send):
        for _ in range(2):
            try:
                await receive()
            except Exception as e:  # noqa: BLE001
                reads.append(type(e).__name__)
        await send({"type": "http.response.start", "status": 400, "headers": []})

    async def receive():
        return {"type": "http.request", "body": b"x" * 8, "more_body": True}

    async def send(message):
        out.append(message)

    await BodySizeLimit(app, max_body=4)({"type": "http", "headers": []}, receive, send)
    assert reads == ["_BodyTooLarge", "_BodyTooLarge"]
    assert [m.get("status") for m in out if m["type"] == "http.response.start"] == [413]
