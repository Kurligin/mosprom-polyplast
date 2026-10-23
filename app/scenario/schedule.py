"""Расписание шагов для новичка: чистые функции без БД."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.scenario.models import EmployeeCtx, PlannedStep, StepSpec, TimingRule
from app.scenario.workcal import next_workday, nth_workday, prev_workday

DEFAULT_TIME = time(10, 0)
# Шаг, чьё время прошло больше чем на порог, не отправляем (устарел)
STALE_AFTER = timedelta(days=1)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def step_date(rule: TimingRule, emp: EmployeeCtx) -> date | None:
    """Дата шага по правилу. None — шаг по событию, в расписание не идёт.

    Всё считается от первого рабочего дня: если HR указал датой выхода выходной или праздник,
    «завтра первый рабочий день» и «через месяц» — от понедельника (или первого дня после праздника).
    """
    first = next_workday(emp.start_date)
    if rule.kind == "before_start":
        # календарные дни до первого рабочего дня, без переноса: «завтра первый рабочий день» в воскресенье — верно
        return first - timedelta(days=rule.days_before or 1)
    if rule.kind == "workday":
        return nth_workday(first, rule.n or 1)
    if rule.kind == "calendar_offset":
        d = add_months(first, rule.months) + timedelta(weeks=rule.weeks, days=rule.days)
        return next_workday(d)
    if rule.kind == "month_start":
        # первый день M-го месяца работы = первый рабочий день + (M-1) месяцев
        return next_workday(add_months(first, (rule.month or 1) - 1))
    if rule.kind == "probation_end":
        d = emp.probation_end + timedelta(days=rule.offset_days)
        # «последний день испытательного срока» выпал на выходной — последний рабочий день срока
        return prev_workday(d) if rule.offset_days <= 0 else next_workday(d)
    return None


def _time(rule: TimingRule) -> time:
    if not rule.time:
        return DEFAULT_TIME
    h, m = rule.time.split(":")
    return time(int(h), int(m))


def plan_for(employee: EmployeeCtx, steps: list[StepSpec]) -> list[PlannedStep]:
    """Плановое время всех шагов новичка (по площадке и типу работы)."""
    tz = ZoneInfo(employee.tz)
    out: list[PlannedStep] = []
    for s in steps:
        if not s.timing or not s.applies_to(employee.site_code, employee.work_type):
            continue
        d = step_date(s.timing, employee)
        if d is None:
            continue
        out.append(PlannedStep(step_number=s.number, due_at=datetime.combine(d, _time(s.timing), tzinfo=tz),
                               gate=s.gate))
    out.sort(key=lambda p: (p.due_at, p.step_number))
    return out


def employee_now(employee: EmployeeCtx, now: datetime) -> datetime:
    """Текущее время новичка с учётом «машины времени» (смещение часов)."""
    return now.astimezone(ZoneInfo(employee.tz)) + timedelta(hours=employee.time_shift_hours)


def due_steps(employee: EmployeeCtx, steps: list[StepSpec], now: datetime, sent: set[int],
              gates: set[str] | frozenset[str] = frozenset(),
              fresh: set[int] | frozenset[int] = frozenset()) -> list[PlannedStep]:
    """Шаги для тика планировщика: время наступило, не отправлены, не устарели, отмашка есть.

    Шаги «до выхода» не устаревают до первого рабочего дня: новичка, добавленного накануне, всё равно знакомим.
    gates — открытые отмашки новичка (recruiter_go, plan_ready).
    fresh — шаги, которые сейчас не устаревают: реплики после опроса, который новичок прошёл недавно.
    """
    vnow = employee_now(employee, now)
    by_num = {s.number: s for s in steps}
    tz = ZoneInfo(employee.tz)
    start_dt = datetime.combine(next_workday(employee.start_date), time(0, 0), tzinfo=tz)
    res = []
    for p in plan_for(employee, steps):
        if p.step_number in sent or p.due_at > vnow:
            continue
        if p.gate and p.gate not in gates:
            continue
        rule = by_num[p.step_number].timing
        onboarding = rule is not None and rule.kind == "before_start" and vnow < start_dt
        # шаг по отмашке не устаревает: отмашка может прийти позже плана
        if vnow - p.due_at > STALE_AFTER and not onboarding and not p.gate and p.step_number not in fresh:
            continue
        res.append(p)
    return res


def branch_targets(steps: list[StepSpec], step_number: int, label: str,
                   employee: EmployeeCtx) -> list[StepSpec]:
    """Шаги-ответы на кнопку (например «Да!» → файл «где пообедать» своей площадки)."""
    res = []
    for s in steps:
        t = s.timing
        if t and t.kind == "event" and t.event == "answer" and t.after_step == step_number \
                and (t.answer or "").lower() in label.lower() \
                and s.applies_to(employee.site_code, employee.work_type):
            res.append(s)
    return res
