"""Два режима ИИ: облачный GigaChat по API и локальная модель в закрытом контуре (OpenAI-совместимый API)."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.llm import gigachat as llm

pytestmark = pytest.mark.asyncio

CANDS = [{"chunk_id": 11, "text": "6.6.1. Перерыв для отдыха и питания: 13:00 – 13:48."},
         {"chunk_id": 12, "text": "6.7.4. Ежегодный отпуск 28 календарных дней."}]


def _local_settings(**kw):
    base = {"llm_provider": "openai", "llm_base_url": "http://llm.local:8000/v1", "llm_model": "local-model",
                "llm_api_key": "", "gigachat_credentials": "", "gigachat_scope": "", "gigachat_model": "GigaChat-2",
                "ca_bundle_file": ""}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def local_server(monkeypatch):
    """Фейковый OpenAI-совместимый сервер внутри контура: запоминает запросы, выбирает пункт 11."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"chunk_id": 11})}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))
    return calls


async def test_local_model_in_closed_contour(monkeypatch, local_server):
    monkeypatch.setattr(llm, "_settings", lambda: _local_settings())
    chosen = await llm.choose_chunk("сколько длится обед", CANDS, raise_errors=True)
    assert chosen == 11
    req = local_server[-1]
    assert str(req.url) == "http://llm.local:8000/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "local-model"
    assert "Authorization" not in req.headers


async def test_local_model_with_api_key(monkeypatch, local_server):
    monkeypatch.setattr(llm, "_settings", lambda: _local_settings(llm_api_key="secret-key"))
    await llm.choose_chunk("сколько длится обед", CANDS, raise_errors=True)
    assert local_server[-1].headers["Authorization"] == "Bearer secret-key"


async def test_default_provider_is_gigachat(monkeypatch):
    monkeypatch.setattr(llm, "_settings", lambda: _local_settings(llm_provider=""))
    assert llm._provider() == "gigachat"
    assert llm._model_name() == "GigaChat-2"
