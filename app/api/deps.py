"""Общие зависимости API: сессия БД, загрузка файлов, вызов модулей, которые пишут параллельно."""

from __future__ import annotations

import importlib
import inspect
import logging
import re
import secrets
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session

log = logging.getLogger(__name__)

UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "data" / "uploads"
MAX_UPLOAD = 50 * 1024 * 1024


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


# --- модули других команд: импорт по требованию ---------------------------------

def optional(module: str, attr: str):
    """Функция модуля или None, если модуля/функции ещё нет или он не загрузился."""
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if not (e.name and module.startswith(e.name)):
            log.warning("Модуль %s не загрузился: %s", module, e)
        return None
    except Exception as e:  # noqa: BLE001 — сломанный модуль не должен ронять API
        log.warning("Модуль %s не загрузился: %s", module, e)
        return None
    return getattr(mod, attr, None)


def need(module: str, attr: str, what: str):
    """Функция модуля; нет — 501 с понятным текстом."""
    fn = optional(module, attr)
    if fn is None:
        raise HTTPException(501, f"{what} ещё не готов ({module}.{attr})")
    return fn


async def call(fn, *args, **kwargs):
    """Вызвать функцию, синхронную или асинхронную."""
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


# --- загрузка файлов ---------------------------------------------------------------

async def save_upload(file: UploadFile, sub: str, allowed: tuple[str, ...]) -> Path:
    """Файл из multipart → data/uploads/<sub>/<время>-<случайное>-<имя>. Абсолютный путь."""
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Нужен файл {' или '.join(a.lstrip('.').upper() for a in allowed)}")
    safe = re.sub(r"[^\w.\-]+", "_", name)[-120:] or f"file{ext}"
    dest_dir = UPLOAD_ROOT / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}-{safe}"
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Файл больше 50 МБ")
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Файл пустой")
    return dest
