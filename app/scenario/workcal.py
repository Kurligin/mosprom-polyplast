"""Производственный календарь РФ (config/calendar_ru.json)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

CALENDAR_PATH = Path(__file__).resolve().parents[2] / "config" / "calendar_ru.json"

# Фолбэк для лет вне календаря: праздники ст. 112 ТК РФ без переносов
_FIXED_HOLIDAYS = {(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
                   (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}


@lru_cache(maxsize=1)
def _load() -> tuple[frozenset[date], frozenset[date], frozenset[int]]:
    data = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    off, work = set(), set()
    for y in data["years"].values():
        off.update(date.fromisoformat(d) for d in y["days_off"])
        work.update(date.fromisoformat(d) for d in y["work_weekends"])
    years = frozenset(int(y) for y in data["years"])
    return frozenset(off), frozenset(work), years


def is_workday(d: date) -> bool:
    off, work, years = _load()
    if d.year not in years:
        return d.weekday() < 5 and (d.month, d.day) not in _FIXED_HOLIDAYS
    if d in work:
        return True
    if d.weekday() >= 5:
        return False
    return d not in off


def next_workday(d: date) -> date:
    """Ближайший рабочий день, начиная с d включительно."""
    while not is_workday(d):
        d += timedelta(days=1)
    return d


def prev_workday(d: date) -> date:
    """Ближайший рабочий день, начиная с d включительно и назад."""
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


def nth_workday(start: date, n: int) -> date:
    """N-й рабочий день, считая от start (первый рабочий день выхода = 1)."""
    if n < 1:
        raise ValueError("n >= 1")
    d = next_workday(start)
    for _ in range(n - 1):
        d = next_workday(d + timedelta(days=1))
    return d
