"""Голосовые сообщения (app/bot/voice.py) на настоящей БД, без модели: распознаватель подменён.

Голосовое → «Слушаю…» → правка на «🎙 «текст»» → тот же маршрут, что у набранного текста
(красный флаг, база знаний, персонал). Ошибка, таймаут и слишком длинная запись → вежливая просьба.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types

import httpx
import pytest
from sqlalchemy import select

from app.bot import hooks, voice
from app.bot import texts as BT
from app.bot.api import DownloadTooLarge, MaxApi, MaxApiError
from app.bot.handlers import VOICE_LOG, handle_update
from app.config import settings
from app.db.models import Flag, Message
from tests.surveys.fakes import ev_text

pytestmark = pytest.mark.asyncio

URL = "https://vu.mycdn.me/voice/abc.ogg"
RED = "Мастер угрожает уволить, если пожалуюсь"


def _quiet_import(name: str):
    """Модуль hooks без следа в общем реестре бота: другие тесты не должны его видеть."""
    saved = (dict(hooks._callbacks), dict(hooks._states), dict(hooks._commands), list(hooks._pre_routes),
             dict(hooks._single))
    try:
        return importlib.import_module(name)
    finally:
        for target, value in zip((hooks._callbacks, hooks._states, hooks._commands, hooks._pre_routes,
                                  hooks._single), saved, strict=True):
            target.clear()
            target.extend(value) if isinstance(target, list) else target.update(value)


signal_hooks = _quiet_import("app.signals.hooks")


def ev_voice(user_id: int = 100, chat_id: int = 500, text: str = "") -> dict:
    upd = ev_text(text, user_id, chat_id)
    upd["message"]["body"]["attachments"] = [{"type": "audio", "payload": {"url": URL, "token": "tok"}}]
    return upd


@pytest.fixture
def stt(world, monkeypatch):
    """Подмена скачивания и распознавания; распознанный текст обрабатывается в сессии теста."""
    monkeypatch.setattr(settings, "voice_enabled", True)
    monkeypatch.setattr(voice, "available", lambda: True)
    state = types.SimpleNamespace(text="", error=None, gate=None, downloads=[], processed=[])

    async def download(ctx, url):
        state.downloads.append(url)
        return b"OggS-fake"

    async def transcribe(data):
        if state.gate is not None:
            await state.gate.wait()
        if state.error is not None:
            raise state.error
        return state.text

    async def process_text(ctx, upd):
        state.processed.append(upd)
        await handle_update(ctx, world.repo, upd)
        await world.session.flush()

    monkeypatch.setattr(voice, "download", download)
    monkeypatch.setattr(voice, "transcribe", transcribe)
    monkeypatch.setattr(voice, "process_text", process_text)
    return state


async def _send_voice(w, **kw) -> str:
    """Голосовое от новичка; → mid сообщения «Слушаю…»."""
    await handle_update(w.ctx, w.repo, ev_voice(**kw))
    await w.session.flush()
    mid = w.api.last(kw.get("chat_id", 500))
    assert w.api.text(mid) == "Слушаю…"
    await voice.drain()
    return mid


async def test_red_flag_from_voice(world, stt, monkeypatch):
    monkeypatch.setattr(hooks, "_pre_routes", [signal_hooks.red_flag_in_message])
    stt.text = RED
    mid = await _send_voice(world)
    assert world.api.text(mid) == f"🎙 «{RED}»"
    flags = (await world.session.scalars(select(Flag).where(Flag.employee_id == world.emp.id,
                                                            Flag.level == "red"))).all()
    assert flags and all(f.source == "message" for f in flags)
    assert "увидит только HR" in world.api.text(world.api.last(500))
    assert stt.downloads == [URL]


async def test_voice_goes_where_text_goes(world, stt, monkeypatch):
    """Вопрос голосом попадает в тот же обработчик базы знаний, что и набранный."""
    calls = []

    async def free_text(ctx, repo, emp, text):
        calls.append((emp.id, text))

    monkeypatch.setitem(hooks._single, "free_text", free_text)
    stt.text = "Где получить спецодежду?"
    mid = await _send_voice(world)
    await world.say("Где получить спецодежду?")
    assert calls == [(world.emp.id, "Где получить спецодежду?")] * 2
    assert world.api.text(mid) == "🎙 «Где получить спецодежду?»"
    body = stt.processed[0]["message"]["body"]
    assert body["text"] == "Где получить спецодежду?" and "attachments" not in body
    logged = (await world.session.scalars(select(Message.text).where(
        Message.employee_id == world.emp.id, Message.direction == "in").order_by(Message.id))).all()
    assert logged[:2] == [VOICE_LOG, "Где получить спецодежду?"]


async def test_voice_from_staff_goes_to_staff_text(world, stt, monkeypatch):
    world.rec.max_user_id, world.rec.chat_id = 200, 600
    await world.session.flush()
    calls = []

    async def staff_text(ctx, repo, staff, text, body):
        calls.append((staff.id, text))

    monkeypatch.setitem(hooks._single, "staff_text", staff_text)
    stt.text = "Покажи новичков"
    mid = await _send_voice(world, user_id=200, chat_id=600)
    assert world.api.text(mid) == "🎙 «Покажи новичков»"
    assert calls == [(world.rec.id, "Покажи новичков")]


async def test_long_polling_not_blocked(world, stt):
    """Обработчик события отвечает «Слушаю…» и возвращается, не дожидаясь распознавания."""
    stt.gate, stt.text = asyncio.Event(), "Привет"
    await handle_update(world.ctx, world.repo, ev_voice())
    mid = world.api.last(500)
    assert world.api.text(mid) == "Слушаю…" and not stt.processed
    stt.gate.set()
    await voice.drain()
    assert world.api.text(mid) == "🎙 «Привет»" and stt.processed


@pytest.mark.parametrize("error", [RuntimeError("модель сломалась"), MaxApiError(404, "download.failed")])
async def test_recognition_error_asks_for_text(world, stt, error):
    stt.error = error
    mid = await _send_voice(world)
    assert world.api.text(mid) == "Не разобрал, напиши, пожалуйста, текстом."
    assert not stt.processed


async def test_empty_recognition_asks_for_text(world, stt):
    world.emp.address_form = "vy"
    stt.text = ""
    mid = await _send_voice(world)
    assert world.api.text(mid) == "Не разобрал, напишите, пожалуйста, текстом."
    assert not stt.processed


async def test_timeout_asks_for_text(world, stt, monkeypatch):
    monkeypatch.setattr(settings, "voice_timeout", 0.05)
    stt.gate = asyncio.Event()  # распознавание «зависло»
    mid = await _send_voice(world)
    assert world.api.text(mid) == BT.tv(world.emp, BT.VOICE_FAILED)
    assert not stt.processed


@pytest.mark.parametrize("error", [voice.VoiceTooLong("75 с"), DownloadTooLarge(20_000_000)])
async def test_too_long_asks_shorter(world, stt, error):
    stt.error = error
    mid = await _send_voice(world)
    assert world.api.text(mid) == ("Длинновато для меня. Запиши, пожалуйста, покороче, до минуты, "
                                   "или напиши текстом.")
    assert not stt.processed


async def test_voice_disabled_behaves_as_before(world, stt, monkeypatch):
    monkeypatch.setattr(settings, "voice_enabled", False)
    await handle_update(world.ctx, world.repo, ev_voice())
    await voice.drain()
    assert world.api.text(world.api.last(500)) == BT.tv(world.emp, BT.NON_TEXT)
    assert not stt.downloads and not stt.processed


async def test_voice_with_caption_is_text(world, stt, monkeypatch):
    """Текст рядом с аудио — обычное сообщение: файл не распознаём."""
    calls = []

    async def free_text(ctx, repo, emp, text):
        calls.append(text)

    monkeypatch.setitem(hooks._single, "free_text", free_text)
    await handle_update(world.ctx, world.repo, ev_voice(text="Послушай мою запись"))
    await voice.drain()
    assert calls == ["Послушай мою запись"] and not stt.downloads


# --- распознавание без модели: подменённый faster_whisper ------------------------------------

@pytest.fixture
def fake_whisper(monkeypatch):
    """Пакет faster_whisper-заглушка: decode_audio отдаёт `seconds` тишины, модель — заданные сегменты."""
    state = types.SimpleNamespace(seconds=5.0, segments=[" Привет,", " Корсик. "], kw=None)
    audio_mod = types.ModuleType("faster_whisper.audio")
    audio_mod.decode_audio = lambda f, sampling_rate: [0.0] * int(state.seconds * sampling_rate)
    pkg = types.ModuleType("faster_whisper")
    pkg.audio = audio_mod
    monkeypatch.setitem(sys.modules, "faster_whisper", pkg)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_mod)

    class Model:
        def transcribe(self, audio, **kw):
            state.kw = kw
            return (types.SimpleNamespace(text=t) for t in state.segments), None

    monkeypatch.setattr(voice, "_model", Model())
    return state


async def test_transcribe_sync_joins_segments(fake_whisper):
    assert voice.transcribe_sync(b"x") == "Привет, Корсик."
    assert fake_whisper.kw["language"] == "ru" and fake_whisper.kw["vad_filter"] is True


async def test_transcribe_sync_too_long(fake_whisper, monkeypatch):
    monkeypatch.setattr(settings, "voice_max_seconds", 60)
    fake_whisper.seconds = 61
    with pytest.raises(voice.VoiceTooLong):
        voice.transcribe_sync(b"x")
    assert fake_whisper.kw is None, "длинную запись не распознаём"


async def test_model_loads_once_from_local_cache(monkeypatch):
    created = []

    class WhisperModel:
        def __init__(self, name, **kw):
            created.append((name, kw))

    pkg = types.ModuleType("faster_whisper")
    pkg.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", pkg)
    monkeypatch.setattr(voice, "_model", None)
    monkeypatch.setattr(settings, "voice_model", "medium")
    assert voice.get_model() is voice.get_model()
    assert len(created) == 1
    name, kw = created[0]
    assert name == "medium" and kw["compute_type"] == "int8" and kw["local_files_only"] is True


async def test_process_text_uses_runner(monkeypatch):
    from app.bot import runner

    seen = []

    async def process_update(ctx, upd):
        seen.append(upd)

    monkeypatch.setattr(runner, "process_update", process_update)
    await voice.process_text("ctx", {"update_type": "message_created"})
    assert seen == [{"update_type": "message_created"}]


async def test_audio_attachment_detection(monkeypatch):
    monkeypatch.setattr(settings, "voice_enabled", True)
    monkeypatch.setattr(voice, "available", lambda: True)
    body = ev_voice()["message"]["body"]
    assert voice.is_voice(body, "")
    assert not voice.is_voice(body, "текст")
    assert not voice.is_voice({"attachments": [{"type": "image", "payload": {"url": URL}}]}, "")
    assert not voice.is_voice({"attachments": [{"type": "audio", "payload": {}}]}, "")
    monkeypatch.setattr(voice, "available", lambda: False)
    assert not voice.is_voice(body, ""), "без пакета faster-whisper — как раньше"


# --- скачивание вложения: MaxApi.download ------------------------------------------------------

def _api(handler) -> MaxApi:
    client = httpx.AsyncClient(base_url="https://max.test", headers={"Authorization": "TOKEN"},
                               transport=httpx.MockTransport(handler))
    return MaxApi("TOKEN", "https://max.test", client=client)


async def test_download_without_bot_token_to_cdn():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"audio")

    api = _api(handler)
    try:
        assert await api.download(URL, max_bytes=100) == b"audio"
        assert await api.download("https://max.test/files/1", max_bytes=100) == b"audio"
    finally:
        await api.close()
    assert "authorization" not in seen[0].headers, "токен бота не уходит на CDN"
    assert seen[1].headers["authorization"] == "TOKEN"


async def test_download_size_limit_and_errors():
    async def chunks():
        yield b"x" * 30
        yield b"x" * 30

    def handler(request):
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 50)
        if request.url.path == "/stream":
            return httpx.Response(200, content=chunks())
        return httpx.Response(404)

    api = _api(handler)
    try:
        with pytest.raises(DownloadTooLarge):
            await api.download("https://cdn.test/big", max_bytes=10)
        with pytest.raises(DownloadTooLarge):
            await api.download("https://cdn.test/stream", max_bytes=40)
        with pytest.raises(MaxApiError) as e:
            await api.download("https://cdn.test/none", max_bytes=10)
        assert e.value.status == 404
    finally:
        await api.close()
