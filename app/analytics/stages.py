"""Этапы адаптации для воронки: до выхода, 1-я неделя, 1-й месяц, 2-й, 3-й, завершили."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

STAGES = (
    ("pre", "До выхода"),
    ("week1", "1-я неделя"),
    ("month1", "1-й месяц"),
    ("month2", "2-й месяц"),
    ("month3", "3-й месяц"),
    ("done", "Завершили"),
)
STAGE_LABELS = dict(STAGES)
DONE_RECENT_DAYS = 90  # «Завершили» — испытательный закончился за последние 3 месяца


def stage_of(day: int, probation_days: int, status: str = "active") -> str:
    """Этап по дню адаптации (1 — день выхода). Испытательный длиннее 3 месяцев — тоже «3-й месяц»."""
    if status == "finished" or day > probation_days:
        return "done"
    if day <= 0:
        return "pre"
    if day <= 7:
        return "week1"
    if day <= 30:
        return "month1"
    if day <= 60:
        return "month2"
    return "month3"


def site_tz(emp) -> ZoneInfo:
    site = vars(emp).get("site") if hasattr(emp, "__dict__") else None
    return ZoneInfo(getattr(site, "tz", None) or settings.tz_default)


def employee_today(emp, now: datetime) -> date:
    """«Сегодня» новичка: часовой пояс площадки и машина времени."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (now + timedelta(hours=emp.time_shift_hours or 0)).astimezone(site_tz(emp)).date()


def first_day(emp) -> date:
    """Первый рабочий день, как у движка сценария; без движка — дата выхода."""
    try:
        from app.scenario.engine import first_day as engine_first_day

        return engine_first_day(emp)
    except Exception:  # noqa: BLE001 — движок правит другая команда, аналитика не должна падать
        return emp.start_date


def probation_end(emp) -> date:
    days = probation_days(emp)
    return first_day(emp) + timedelta(days=days - 1)


def adaptation_day(emp, now: datetime) -> int:
    from app.tickets.service import employee_day

    return employee_day(emp, now)


def probation_days(emp) -> int:
    from app.tickets.service import probation_days as pdays

    return pdays(emp)
