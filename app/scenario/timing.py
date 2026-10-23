"""Разбор колонки «Месяц/день/время информирования» в TimingRule правилами."""
from __future__ import annotations

import re

from app.scenario.models import TimingRule

ORDINALS = {
    "перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5,
    "шест": 6, "седьм": 7, "восьм": 8, "девят": 9, "десят": 10,
}

_TIME_RE = re.compile(r"(?:\bв\s*)?\b(\d{1,2})[:.](\d{2})\b", re.IGNORECASE)
_OFFICE_RE = re.compile(r"для\s+(?:сотрудников\s+офиса|офисных)", re.IGNORECASE)


def _norm(s: str) -> str:
    s = s.replace(" ", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", s).strip()


def _ordinal(word: str) -> int | None:
    w = word.lower()
    for stem, n in ORDINALS.items():
        if w.startswith(stem):
            return n
    return None


def extract_time(text: str) -> str | None:
    """«в 10:00», «В 12.30», «13.00» → «HH:MM»."""
    m = _TIME_RE.search(text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def is_office_only(*texts: str | None) -> bool:
    return any(t and _OFFICE_RE.search(_norm(t)) for t in texts)


def parse_timing(raw: str | None) -> TimingRule | None:
    """Текст времени → правило. None — не разобрано (или пусто)."""
    if not raw or not raw.strip():
        return None
    s = _norm(raw)
    low = s.lower()
    time = extract_time(s)

    # по событию
    m = re.search(r"в случае варианта ответа\s*[«\"](.+?)[»\"]", low)
    if m:
        return TimingRule(kind="event", event="answer", answer=m.group(1).strip().capitalize())
    if low.startswith("если") and re.search(r"\bопрос", low):
        return TimingRule(kind="event", event="survey_reminder")
    if low.startswith("если") and "вопрос" in low:
        return TimingRule(kind="event", event="free_question")

    # за N(-M) дней / за день до выхода
    m = re.search(r"за\s+(?:(\d+)\s*-\s*)?(\d+)?\s*(день|дня|дней)\s+до\s+выхода", low)
    if m:
        n = int(m.group(2)) if m.group(2) else 1
        return TimingRule(kind="before_start", days_before=n, time=time)

    # N-й рабочий день
    m = re.search(r"(?:в|во)?\s*(\w+)\s+рабочий\s+день", low)
    if m and _ordinal(m.group(1)):
        return TimingRule(kind="workday", n=_ordinal(m.group(1)), time=time)

    # первый день M-го месяца (работы)
    m = re.search(r"первый\s+день\s+(\w+)\s+месяца", low)
    if m and _ordinal(m.group(1)):
        return TimingRule(kind="month_start", month=_ordinal(m.group(1)), time=time)

    # последний день ИС / испытательного срока
    if re.search(r"последн\w*\s+день\s+(ис\b|испытат)", low):
        return TimingRule(kind="probation_end", offset_days=0, time=time)

    # через N месяцев и M недель / N дней
    if low.startswith("через"):
        months = weeks = days = 0
        for num, unit in re.findall(r"(\d+)\s*(месяц\w*|недел\w*|дн\w*|день)", low):
            if unit.startswith("месяц"):
                months += int(num)
            elif unit.startswith("недел"):
                weeks += int(num)
            else:
                days += int(num)
        if months or weeks or days:
            return TimingRule(kind="calendar_offset", months=months, weeks=weeks, days=days, time=time)

    return None


def is_time_only(raw: str | None) -> bool:
    """«В 09.01» — только время, дата берётся от предыдущего шага."""
    if not raw:
        return False
    return bool(re.fullmatch(r"(в\s*)?\d{1,2}[:.]\d{2}\.?", _norm(raw).lower()))
