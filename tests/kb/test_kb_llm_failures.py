"""Сбои ИИ (app/llm/gigachat.py): HTTP-ошибки и таймаут локальной модели, битый JSON, журнал llm_log без текстов.

Ни одного настоящего запроса: GigaChat и OpenAI-совместимый сервер подменены. Тексты синтетические.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest

import app.db.session as db_session
from app.db.models import LlmLog
from app.llm import gigachat as g
from app.privacy import names as privacy_names

pytestmark = pytest.mark.asyncio

CANDS = [{"chunk_id": 11, "text": "6.6.1. Перерыв для отдыха и питания: 13:00 – 13:48."},
         {"chunk_id": 12, "text": "6.7.4. Ежегодный отпуск 28 календарных дней."}]
SECRET_Q = "Иванов Пётр спрашивает: сколько длится обед? тел. +7 916 123-45-67"


@pytest.fixture(autouse=True)
def _no_db_names(monkeypatch):
    async def none(*a, **k):
        return []

    monkeypatch.setattr(privacy_names, "known_names", none)


def _local(monkeypatch, handler, **kw):
    """Провайдер openai (локальная модель в контуре) на фейковом сервере."""
    base = {"llm_provider": "openai", "llm_base_url": "http://llm.local:8000/v1", "llm_model": "local-model",
            "llm_api_key": "", "gigachat_credentials": "", "gigachat_scope": "", "gigachat_model": "GigaChat-2",
            "ca_bundle_file": ""}
    base.update(kw)
    monkeypatch.setattr(g, "_settings", lambda: SimpleNamespace(**base))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))


def _ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _fake(responses, seen):
    it = iter(responses)

    async def complete(system, user, temperature=0.01):
        seen.append(user)
        item = next(it)
        if isinstance(item, BaseException):
            raise item
        return item

    return complete


# --- локальная модель: ошибки и таймаут --------------------------------------------------------


@pytest.mark.parametrize("response, err", [
    (httpx.Response(500, text="internal error"), "HTTPStatusError"),
    (httpx.Response(503, text="model is loading"), "HTTPStatusError"),
    (httpx.Response(200, json={"error": "no choices"}), "KeyError"),
])
async def test_local_model_errors_raise_unavailable(monkeypatch, response, err):
    _local(monkeypatch, lambda request: response)
    with pytest.raises(g.LlmUnavailable, match=err):
        await g.choose_chunk("сколько длится обед", CANDS, raise_errors=True)
    assert await g.choose_chunk("сколько длится обед", CANDS) is None, "без raise_errors сбой = «не знаю»"


async def test_local_model_network_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    _local(monkeypatch, handler)
    with pytest.raises(g.LlmUnavailable, match="ConnectError"):
        await g.choose_chunk("сколько длится обед", CANDS, raise_errors=True)


async def test_local_model_without_base_url(monkeypatch):
    calls = []
    _local(monkeypatch, lambda request: calls.append(request) or _ok("{}"), llm_base_url="")
    with pytest.raises(g.LlmUnavailable, match="RuntimeError"):
        await g.choose_chunk("сколько длится обед", CANDS, raise_errors=True)
    assert calls == []


async def test_timeout_raises_unavailable_and_releases_queue(monkeypatch):
    """Зависшая модель → LlmUnavailable по таймауту; очередь вызовов (семафор) после этого свободна."""
    slow = {"on": True}

    async def handler(request):
        if slow["on"]:
            await asyncio.sleep(5)
        return _ok(json.dumps({"chunk_id": 12}))

    _local(monkeypatch, handler)
    with pytest.raises(g.LlmUnavailable, match="TimeoutError"):
        await g.choose_chunk("сколько дней отпуск", CANDS, raise_errors=True, timeout=0.05)
    slow["on"] = False
    assert await asyncio.wait_for(g.choose_chunk("сколько дней отпуск", CANDS, timeout=1), 2) == 12


async def test_local_model_null_content_is_bad_json(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok(None)

    _local(monkeypatch, handler)
    assert await g.choose_chunk("сколько длится обед", CANDS, raise_errors=True) is None
    assert len(calls) == 2, "пустой ответ — один повтор, потом «не знаю», без исключения"


# --- битый JSON и ответы модели ------------------------------------------------------------------


async def test_bad_json_twice_is_none(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(["пункт 11", '{"answer": 11}'], seen))
    assert await g.choose_chunk("обед", CANDS, raise_errors=True) is None
    assert len(seen) == 2


@pytest.mark.parametrize("raw, expected", [
    ('```json\n{"chunk_id": "11"}\n```', 11),
    ('Ответ: {"chunk_id": "[12]"}', 12),
    ('{"chunk_id": null}', None),
    ('{"chunk_id": "null"}', None),
    ('{"chunk_id": ""}', None),
    ('{"chunk_id": 99}', None),
])
async def test_model_answers(monkeypatch, raw, expected):
    monkeypatch.setattr(g, "_complete", _fake([raw], []))
    assert await g.choose_chunk("обед", CANDS) == expected


@pytest.mark.parametrize("raw, expected", [
    ("", None),
    ("нет json", None),
    ("[1, 2]", None),
    ('{"a": 1', None),
    ('{"chunk_id": 11,}', None),
    ('```JSON {"a": 1} ```', {"a": 1}),
])
async def test_parse_json_object(raw, expected):
    assert g.parse_json_object(raw) == expected


async def test_analyze_answer_llm_error_is_none(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake([httpx.ConnectError("нет сети")], seen))
    assert await g.analyze_answer("Пропуск до сих пор не сделали") is None
    assert len(seen) == 1, "сетевой сбой без повтора"


async def test_analyze_answer_bad_json_retry(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(["не JSON", "опять не JSON"], seen))
    assert await g.analyze_answer("Пропуск до сих пор не сделали") is None
    assert len(seen) == 2
    monkeypatch.setattr(g, "_complete", _fake(["не JSON", '{"topic": "Доступы", "mood": "НЕГАТИВ"}'], []))
    assert await g.analyze_answer("Пропуск до сих пор не сделали") == {
        "topic": "доступы", "mood": "негатив", "quote": None}


async def test_analyze_answer_unknown_topic_and_mood(monkeypatch):
    monkeypatch.setattr(g, "_complete", _fake(['{"topic": "погода", "mood": "восторг", "quote": "«всё хорошо»"}'], []))
    assert await g.analyze_answer("Пока всё хорошо, спасибо") == {
        "topic": "другое", "mood": "нейтрально", "quote": "всё хорошо"}


async def test_analyze_answer_empty_text_not_sent(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake([], seen))
    assert await g.analyze_answer("") is None
    assert await g.analyze_answer("   ") is None
    assert seen == []


async def test_sensitive_list_unavailable_falls_back_to_detector(monkeypatch):
    """Сломан список sensitive из настроек — работает общий детектор здоровья, разбор не ломается."""
    from app.signals import rules

    def broken(text, rules=None):
        raise FileNotFoundError("config/flag_rules.yaml")

    monkeypatch.setattr(rules, "is_sensitive", broken)
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(['{"topic": "нагрузка", "mood": "негатив"}'], seen))
    assert (await g.analyze_answer("Очень много задач на смене"))["topic"] == "нагрузка"
    assert len(seen) == 1
    seen2 = []
    monkeypatch.setattr(g, "_complete", _fake([], seen2))
    assert await g.analyze_answer("у меня болит спина после смены") is None and seen2 == []


async def test_names_db_down_still_masks_caller_names(monkeypatch):
    async def down(*a, **k):
        raise OSError("БД недоступна")

    monkeypatch.setattr(privacy_names, "known_names", down)
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(['{"topic": "наставник", "mood": "негатив"}'], seen))
    await g.analyze_answer("Наставник Смирнова Ольга не отвечает", ["Смирнова Ольга"])
    assert "Смирнова" not in seen[0] and "[ФИО]" in seen[0]


# --- клиент GigaChat ---------------------------------------------------------------------------


class FakeGigaChat:
    created: ClassVar[list[dict]] = []

    def __init__(self, **kw):
        FakeGigaChat.created.append(kw)
        self.chats = []

    async def achat(self, chat):
        self.chats.append(chat)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"chunk_id": 11}'))])


@pytest.fixture
def fake_gigachat(monkeypatch):
    import gigachat

    FakeGigaChat.created = []
    monkeypatch.setattr(gigachat, "GigaChat", FakeGigaChat)
    monkeypatch.setattr(g, "_client", None)
    monkeypatch.setattr(g, "_settings", lambda: SimpleNamespace(
        llm_provider="gigachat", gigachat_credentials="CREDS", gigachat_scope="", gigachat_model="GigaChat-2",
        ca_bundle_file="certs/russian_trusted_ca_bundle.pem"))
    return FakeGigaChat


async def test_gigachat_client_settings_and_cache(fake_gigachat):
    client = g.get_client()
    assert g.get_client() is client and len(fake_gigachat.created) == 1, "клиент один на процесс"
    kw = fake_gigachat.created[0]
    assert kw["credentials"] == "CREDS" and kw["scope"] == "GIGACHAT_API_PERS" and kw["model"] == "GigaChat-2"
    assert kw["verify_ssl_certs"] is True
    ca = Path(kw["ca_bundle_file"])
    assert ca.is_absolute() and ca.parts[-2:] == ("certs", "russian_trusted_ca_bundle.pem") and ca.exists()


async def test_gigachat_call_sends_masked_chat(fake_gigachat):
    assert await g.choose_chunk(SECRET_Q, CANDS, ["Иванов Пётр"]) == 11
    chat = g.get_client().chats[-1]
    system, user = chat.messages
    assert system.content == g.CHOOSE_SYSTEM
    assert "916" not in user.content and "Иванов" not in user.content
    assert chat.max_tokens == 300 and chat.temperature < 0.1


async def test_settings_fallback_from_env(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.config", None)
    monkeypatch.setenv("GIGACHAT_MODEL", "GigaChat-Test")
    s = g._settings()
    assert s.gigachat_model == "GigaChat-Test"
    assert g._provider() == "gigachat" and g._model_name() == "GigaChat-Test"


async def test_model_name_for_local_provider(monkeypatch):
    monkeypatch.setattr(g, "_settings", lambda: SimpleNamespace(llm_provider="OpenAI", llm_model="qwen-7b",
                                                                gigachat_model="GigaChat-2"))
    assert g._provider() == "openai" and g._model_name() == "qwen-7b"


# --- журнал llm_log без текстов ---------------------------------------------------------------


async def test_log_line_has_no_texts(monkeypatch, caplog):
    monkeypatch.setattr(g, "_complete", _fake(['{"chunk_id": 11}'], []))
    with caplog.at_level(logging.INFO, logger="llm_log"):
        assert await g.choose_chunk(SECRET_Q, CANDS, ["Иванов Пётр"]) == 11
    line = next(r.getMessage() for r in caplog.records if "llm_call" in r.getMessage())
    assert "type=choose_chunk" in line and "ok=True" in line and "result=chunk" in line
    assert "classes=kb_chunks,name,phone,question" in line
    for leak in ("Иванов", "916", "обед", "13:48", "отпуск"):
        assert leak not in caplog.text, leak


async def test_log_schedules_persist_outside_pytest(monkeypatch):
    rows = []

    async def persist(kind, classes, ms, ok):
        rows.append((kind, classes, ok))

    monkeypatch.setattr(g, "_persist", persist)
    with monkeypatch.context() as m:
        m.delitem(sys.modules, "pytest")  # как в боте: журнал пишется в БД
        g._log("choose_chunk", {"question", "phone"}, 0.0, False, err="TimeoutError")
    await asyncio.gather(*list(g._tasks))
    assert rows == [("choose_chunk", ["phone", "question"], False)]


async def test_log_without_event_loop_is_skipped(monkeypatch):
    called = []
    monkeypatch.setattr(g, "_persist", lambda *a: called.append(a))

    def sync_log():
        with monkeypatch.context() as m:
            m.delitem(sys.modules, "pytest")
            g._log("analyze_answer", set(), 0.0, True)

    await asyncio.to_thread(sync_log)  # в потоке нет цикла событий: запись в БД пропускается
    assert called == []


class _Session:
    def __init__(self, fail=False):
        self.added, self.commits, self.fail = [], 0, fail

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.fail:
            raise OSError("БД недоступна")
        self.commits += 1


async def test_persist_row_is_metadata_only(monkeypatch):
    s = _Session()
    monkeypatch.setattr(db_session, "async_session", s)
    monkeypatch.setattr(g, "_settings", lambda: SimpleNamespace(llm_provider="", gigachat_model="GigaChat-2"))
    await g._persist("choose_chunk", ["name", "question"], 120, True)
    (row,) = s.added
    assert isinstance(row, LlmLog) and s.commits == 1
    assert (row.call_type, row.data_classes, row.model, row.latency_ms, row.ok) == (
        "choose_chunk", ["name", "question"], "GigaChat-2", 120, True)
    cols = set(LlmLog.__table__.columns.keys())
    assert cols == {"id", "created_at", "call_type", "data_classes", "model", "tokens_in", "tokens_out",
                    "latency_ms", "ok"}, "в журнале нет колонок для текстов"


async def test_persist_db_error_does_not_break_answer(monkeypatch):
    monkeypatch.setattr(db_session, "async_session", _Session(fail=True))
    await g._persist("analyze_answer", [], 5, False)  # не бросает
