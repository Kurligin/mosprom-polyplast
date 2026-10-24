"""Голосовые сообщения: «Слушаю…» → скачать → распознать локально → обработать как обычный текст.

Как «МАКС» присылает голосовое (github.com/max-messenger/api-schema, AudioAttachment):
`message.body.attachments[] = {"type": "audio", "payload": {"url": …, "token": …}, "transcription"?}`.
Своё поле `transcription` «МАКС» не используем: распознаём сами, аудио не уходит со своего сервера.

Путь голосового:
1. Обработчик события (`handlers.on_message_created`) сразу отвечает «Слушаю…» и ставит фоновую задачу:
   long polling не ждёт распознавания, события обрабатываются по очереди.
2. Задача скачивает файл в память (лимит размера), распознаёт faster-whisper в отдельном потоке
   (лимит длительности), правит «Слушаю…» на «🎙 «текст»».
3. Распознанный текст идёт тем же путём, что и набранный: то же событие с текстом вместо вложения,
   своя сессия БД и коммит (`runner.process_update`). Красные флаги, опросы, база знаний, /hr, обращения,
   маскирование ПДн перед LLM — всё как у текста.
Ошибка или таймаут — «Не разобрал, напиши текстом», дольше лимита — «запиши покороче». Файл на диск не пишем.

faster-whisper импортируется лениво: без пакета приложение работает, голосовые — как раньше («понимаю только текст»).
"""

from __future__ import annotations

import asyncio
import copy
import functools
import importlib.util
import io
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.bot import texts as T
from app.bot.api import DownloadTooLarge
from app.config import settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

_model = None
_lock = threading.Lock()
# Один поток: распознавания идут по очереди, модель занимает voice_threads ядер, а не все
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice")
_tasks: set[asyncio.Task] = set()
_broken = False      # пакет стоит, но не импортируется: голосовые как раньше
_queued = 0          # голосовых ждут или идут в распознавании
MAX_QUEUE = 3        # больше — сразу «напиши текстом», чтобы очередь не росла


class VoiceBusy(Exception):
    """Распознавания и так в очереди: это голосовое не берём."""


class VoiceTooLong(Exception):
    """Запись длиннее voice_max_seconds."""


def audio_attachment(body: dict) -> dict | None:
    """Первое аудиовложение с url или None."""
    for att in body.get("attachments") or []:
        if isinstance(att, dict) and att.get("type") == "audio" and (att.get("payload") or {}).get("url"):
            return att
    return None


@functools.cache
def available() -> bool:
    """Пакет faster-whisper установлен. Без него голосовые обрабатываются как раньше."""
    return importlib.util.find_spec("faster_whisper") is not None


def is_voice(body: dict, text: str) -> bool:
    """Голосовое без текста, распознавание включено и есть чем. Иначе бот ведёт себя как раньше."""
    return bool(settings.voice_enabled and not text and audio_attachment(body) and available()
                and not _broken)


# --- распознавание ------------------------------------------------------------------------

def get_model():
    """Модель грузится при первом голосовом и живёт до конца процесса. Кэш — HF_HOME в томе /models."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel

                kw = {"device": settings.voice_device, "compute_type": "int8",
                      "cpu_threads": settings.voice_threads}
                started = time.monotonic()
                try:  # модель уже в томе — без запросов к HF Hub
                    m = WhisperModel(settings.voice_model, local_files_only=True, **kw)
                except Exception:  # noqa: BLE001 — первый запуск: скачиваем
                    m = WhisperModel(settings.voice_model, **kw)
                _model = m
                log.info("Модель распознавания %s загружена за %.1f с", settings.voice_model,
                         time.monotonic() - started)
    return _model


def warm_up_in_background() -> None:
    """Загрузить модель при старте бота, чтобы первое голосовое не ждало её загрузки."""
    if not settings.voice_enabled or _model is not None or "pytest" in sys.modules:
        return
    if not available():
        log.warning("faster-whisper не установлен: голосовые не распознаются")
        return

    def _run():
        global _broken
        try:
            get_model()
        except ImportError:  # пакет битый (av, ctranslate2): голосовые как раньше, а не «Не разобрал»
            _broken = True
            log.exception("faster-whisper не импортируется: голосовые не распознаются")
        except Exception:  # без модели голосовое получит «Не разобрал, напиши текстом»
            log.exception("Не удалось загрузить модель распознавания %s", settings.voice_model)

    threading.Thread(target=_run, name="voice-warmup", daemon=True).start()


def transcribe_sync(data: bytes, deadline: float | None = None) -> str:
    """Аудио (ogg/opus, mp3, m4a, wav: PyAV, системный ffmpeg не нужен) → текст. VoiceTooLong — дольше лимита.

    deadline (time.monotonic) уже прошёл, пока ждали поток, — не распознаём: ответ всё равно не дождались."""
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("голосовое ждало очереди дольше таймаута")
    from faster_whisper.audio import decode_audio

    audio = decode_audio(io.BytesIO(data), sampling_rate=SAMPLE_RATE)
    if len(audio) / SAMPLE_RATE > settings.voice_max_seconds:
        raise VoiceTooLong(f"{len(audio) / SAMPLE_RATE:.0f} с")
    segments, _info = get_model().transcribe(
        audio, language="ru", beam_size=settings.voice_beam_size,
        vad_filter=True,                    # тишина не превращается в «Субтитры сделал…»
        condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(data: bytes) -> str:
    """Распознать в отдельном потоке с таймаутом VOICE_TIMEOUT. Поток не прервать, поэтому есть и дедлайн."""
    loop = asyncio.get_running_loop()
    deadline = time.monotonic() + settings.voice_timeout
    fut = loop.run_in_executor(_pool, transcribe_sync, data, deadline)
    return await asyncio.wait_for(fut, timeout=settings.voice_timeout)


async def download(ctx, url: str) -> bytes:
    """Файл голосового в память: тот же клиент «МАКС», повторы на сеть и 5xx, лимит размера."""
    sender = ctx.sender
    return await sender.call(None, sender.api.download, url, max_bytes=settings.voice_max_bytes)


async def process_text(ctx, upd: dict) -> None:
    """Распознанный текст — как обычное событие: своя сессия, обработка, коммит."""
    from app.bot.runner import process_update

    await process_update(ctx, upd)


# --- путь голосового ----------------------------------------------------------------------

def _texts(person) -> dict[str, str]:
    """Тексты на «ты/вы» сразу: объект из сессии события в фоне не читаем."""
    return {"failed": T.tv(person, T.VOICE_FAILED), "too_long": T.tv(person, T.VOICE_TOO_LONG)}


def text_update(upd: dict, text: str) -> dict:
    """То же событие, но с распознанным текстом вместо вложения."""
    new = copy.deepcopy(upd)
    msg = new.setdefault("message", {})
    body = dict(msg.get("body") or {})
    body.pop("attachments", None)
    body["text"] = text
    body["voice"] = True
    msg["body"] = body
    return new


async def start(ctx, repo, person, upd: dict, *, chat_id=None, is_staff: bool = False) -> None:
    """Обработчик события: «Слушаю…» и фоновая задача. Не ждёт распознавания."""
    chat_id = chat_id or person.chat_id
    who = {"staff": person} if is_staff else {"employee": person}
    mids = await ctx.sender.send(repo, T.tv(person, T.VOICE_LISTENING), chat_id=chat_id,
                                 user_id=person.max_user_id, **who)
    if not mids:
        return  # 403: новичок остановил бота
    att = audio_attachment((upd.get("message") or {}).get("body") or {})
    schedule(handle(ctx, upd, att["payload"]["url"], mids[-1], chat_id, _texts(person)))


def schedule(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Дождаться фоновых распознаваний (тесты, остановка)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _recognize(ctx, url: str) -> str:
    """Скачать и распознать; аудио живёт только в памяти этого вызова."""
    global _queued
    if _queued >= MAX_QUEUE:
        raise VoiceBusy(f"в очереди {_queued}")
    _queued += 1
    try:
        data = await asyncio.wait_for(download(ctx, url), timeout=settings.voice_timeout)
        return await transcribe(data)
    finally:
        _queued -= 1


async def _edit(ctx, mid: str, text: str, chat_id) -> None:
    """Правка «Слушаю…». Любая ошибка — в лог: фоновая задача не падает молча."""
    try:
        await ctx.sender.edit(mid, text, key=chat_id, fmt=None)
    except Exception as e:  # noqa: BLE001 — сеть после повторов, клиент закрыт при остановке
        log.warning("Не удалось поправить «Слушаю…» (%s): %s: %s", mid, type(e).__name__, e)


async def handle(ctx, upd: dict, url: str, mid: str, chat_id, texts: dict[str, str]) -> None:
    """Фон: распознать, поправить «Слушаю…», отдать текст обычному маршруту. Ничего не роняет."""
    started = time.monotonic()
    try:
        text = await _recognize(ctx, url)
    except (VoiceTooLong, DownloadTooLarge) as e:
        log.info("Голосовое длиннее лимита: %s", e)
        await _edit(ctx, mid, texts["too_long"], chat_id)
        return
    except asyncio.CancelledError:  # бот останавливается
        await _edit(ctx, mid, texts["failed"], chat_id)
        raise
    except Exception as e:  # noqa: BLE001 — нет пакета, битый файл, сеть, таймаут, очередь
        log.warning("Голосовое не распознано: %s: %s", type(e).__name__, e)
        await _edit(ctx, mid, texts["failed"], chat_id)
        return
    if not text:
        await _edit(ctx, mid, texts["failed"], chat_id)
        return
    log.info("Голосовое распознано за %.1f с (%s символов)", time.monotonic() - started, len(text))
    await _edit(ctx, mid, T.VOICE_HEARD.format(text=text), chat_id)
    try:
        await process_text(ctx, text_update(upd, text))
    except Exception:
        log.exception("Обработка распознанного голосового")
