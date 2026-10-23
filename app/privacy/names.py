"""ФИО всех сотрудников из БД для маскирования перед LLM: персонал (HR, руководители, наставники) и новички.

Синтетические новички — не люди, их «имена» не маскируем: иначе пропадают обычные слова («Модель», «Шаблон»).
Названия и коды площадок из ФИО убираем: «Тестовый Новичок Лужники» — не повод прятать «Лужники».
Список кэшируется на TTL секунд; если БД недоступна, остаётся последний загруженный список.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from app.privacy.mask import clean_name

log = logging.getLogger(__name__)

TTL = 300.0
_cache: dict = {"at": 0.0, "names": [], "loaded": False}


async def load_names(session) -> list[str]:
    """ФИО без пометок в скобках: персонал и новички (кроме синтетических), без названий площадок."""
    from app.db.models import Employee, Site, Staff

    raw: list[str] = []
    raw += [n for n in await session.scalars(select(Staff.full_name)) if n]
    for full, first in await session.execute(
            select(Employee.full_name, Employee.first_name).where(Employee.is_synthetic.is_(False))):
        raw += [n for n in (full, first) if n]
    sites = {w.lower() for code, name in await session.execute(select(Site.code, Site.name))
             for w in f"{code} {name}".split()}
    out: list[str] = []
    for n in raw:
        n = " ".join(w for w in clean_name(n).split() if w.lower() not in sites)
        if n and n not in out:
            out.append(n)
    return out


async def _load(session=None) -> list[str]:
    if session is not None:
        return await load_names(session)
    from app.db.session import async_session

    async with async_session() as s:
        return await load_names(s)


async def known_names(session=None, *, refresh: bool = False) -> list[str]:
    """Имена всех сотрудников (кэш на TTL секунд). session — взять из неё, иначе своя сессия."""
    if not refresh and _cache["loaded"] and time.monotonic() - _cache["at"] < TTL:
        return list(_cache["names"])
    try:
        names = await _load(session)
    except Exception as e:  # noqa: BLE001 — БД недоступна: маскируем по последнему списку
        log.warning("Имена сотрудников для маскирования не загружены: %s", type(e).__name__)
        return list(_cache["names"])
    _cache.update(at=time.monotonic(), names=names, loaded=True)
    return list(names)


def invalidate() -> None:
    """Сбросить кэш (после добавления сотрудника)."""
    _cache.update(at=0.0, names=[], loaded=False)
