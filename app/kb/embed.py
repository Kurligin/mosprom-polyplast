"""Эмбеддинги bge-m3: ленивый синглтон модели, нормализованные векторы 1024.

Модель тяжёлая (~2 ГБ), грузится один раз на процесс; считаем в отдельном потоке,
чтобы не блокировать цикл событий бота и API.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.config import settings

log = logging.getLogger(__name__)

DIM = 1024
BATCH = 16
MAX_SEQ = 1024  # токенов; пункты ПВТР короче, длинные перечни режем строками

_model = None
_lock = threading.Lock()
# Один поток: модель не любит параллельные вызовы, а очередь из пары вопросов не страшна
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed")


def get_model():
    """Модель загружается при первом обращении (или прогреве) и живёт до конца процесса."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                started = time.monotonic()
                try:  # модель уже в кэше тома /models — без запросов к HF Hub
                    m = SentenceTransformer(settings.embedding_model, device="cpu", local_files_only=True)
                except Exception:  # noqa: BLE001 — первый запуск: скачиваем
                    m = SentenceTransformer(settings.embedding_model, device="cpu")
                m.max_seq_length = MAX_SEQ
                _model = m
                log.info("Модель эмбеддингов %s загружена за %.1f с", settings.embedding_model,
                         time.monotonic() - started)
    return _model


def is_loaded() -> bool:
    return _model is not None


def embed_sync(texts: list[str], batch_size: int = BATCH) -> list[list[float]]:
    if not texts:
        return []
    vecs = get_model().encode(list(texts), batch_size=batch_size, normalize_embeddings=True,
                              convert_to_numpy=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


async def embed(texts: list[str], batch_size: int = BATCH) -> list[list[float]]:
    """Векторы для списка текстов (батчами, в отдельном потоке)."""
    if not texts:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, embed_sync, list(texts), batch_size)


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]


def warm_up_in_background() -> None:
    """Загрузить модель заранее, чтобы первый вопрос не ждал 10–20 секунд."""
    if _model is not None:
        return

    def _run():
        try:
            embed_sync(["прогрев"])
        except Exception:  # без модели поиск работает по полнотексту
            log.exception("Не удалось загрузить модель эмбеддингов")

    threading.Thread(target=_run, name="embed-warmup", daemon=True).start()
