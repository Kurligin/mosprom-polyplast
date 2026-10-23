"""Расписание шагов: площадки, офис/производство, переносы, тик планировщика."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.scenario.models import EmployeeCtx, StepSpec, TimingRule
from app.scenario.schedule import STALE_AFTER, branch_targets, due_steps, plan_for

MSK = ZoneInfo("Europe/Moscow")
XLSX = Path("data/raw/korsik_scenario.xlsx")


def emp(site, work_type="production", **kw):
    # выход в пн 2 ноября 2026 (4 ноября — праздник), ИС — 3 месяца
    base = {"start_date": date(2026, 11, 2), "probation_end": date(2027, 2, 1), "site_code": site,
                "work_type": work_type, "tz": "Europe/Moscow"}
    base.update(kw)
    return EmployeeCtx(**base)


def S(n, rule, **kw):
    return StepSpec(number=n, timing=TimingRule(**rule), text=f"шаг {n}", **kw)


STEPS = [
    S(1, {"kind": "before_start", "days_before": 3}),
    S(2, {"kind": "workday", "n": 1, "time": "10:00"}),
    S(3, {"kind": "workday", "n": 1, "time": "11:00"}, site_tags=["GTP"]),
    S(4, {"kind": "workday", "n": 1, "time": "11:00"}, site_tags=["Luzhniki"]),
    S(5, {"kind": "workday", "n": 1, "time": "18:00"}, office_only=True),
    S(6, {"kind": "workday", "n": 3, "time": "17:30"}),
    S(7, {"kind": "calendar_offset", "days": 10}, site_tags=["GrPP", "Luzhniki"]),
    S(8, {"kind": "month_start", "month": 3, "time": "09:00"}),
    S(9, {"kind": "calendar_offset", "months": 2, "weeks": 2}),
    S(10, {"kind": "probation_end", "time": "09:00"}),
    S(11, {"kind": "event", "event": "answer", "after_step": 2, "answer": "Да"}, site_tags=["GTP"]),
    S(12, {"kind": "workday", "n": 2, "time": "10:00"}, gate="recruiter_go"),
]


def dues(plan):
    return {p.step_number: p.due_at for p in plan}


def test_sites_and_work_type():
    gtp = dues(plan_for(emp("GTP"), STEPS))
    luzh = dues(plan_for(emp("Luzhniki", "office"), STEPS))
    assert 3 in gtp and 4 not in gtp and 7 not in gtp and 5 not in gtp
    assert 4 in luzh and 3 not in luzh and 7 in luzh and 5 in luzh
    assert 11 not in gtp  # по событию — не в расписании


def test_dates_and_defaults():
    p = dues(plan_for(emp("GTP"), STEPS))
    assert p[1] == datetime(2026, 10, 30, 10, 0, tzinfo=MSK)  # за 3 дня, 10:00 по умолчанию
    assert p[2] == datetime(2026, 11, 2, 10, 0, tzinfo=MSK)
    assert p[6] == datetime(2026, 11, 5, 17, 30, tzinfo=MSK)  # 3-й рабочий день, 4 ноября пропущен
    assert p[8] == datetime(2027, 1, 11, 9, 0, tzinfo=MSK)    # 2 января 2027 — каникулы → 11 января
    assert p[9] == datetime(2027, 1, 18, 10, 0, tzinfo=MSK)   # 16 января — суббота → понедельник
    assert p[10] == datetime(2027, 2, 1, 9, 0, tzinfo=MSK)
    assert all(d.tzinfo is not None for d in p.values())


def test_weekend_shift_same_time():
    # выход в чт 5 ноября 2026: +10 дней = вс 15-е → пн 16-е, время то же
    p = dues(plan_for(emp("Luzhniki", "office", start_date=date(2026, 11, 5)), STEPS))
    assert p[7] == datetime(2026, 11, 16, 10, 0, tzinfo=MSK)


def test_due_steps_tick():
    e = emp("GTP")
    now = datetime(2026, 11, 2, 10, 5, tzinfo=MSK)
    got = [p.step_number for p in due_steps(e, STEPS, now, sent=set())]
    # день выхода: шаг 1 (за 3 дня) прошёл больше суток назад → устарел
    assert got == [2]
    assert due_steps(e, STEPS, now, sent={2}) == []
    later = now + STALE_AFTER + timedelta(hours=2)
    assert 2 not in [p.step_number for p in due_steps(e, STEPS, later, sent=set())]


def test_before_start_not_stale_until_start():
    e = emp("GTP")
    now = datetime(2026, 11, 1, 20, 0, tzinfo=MSK)  # новичка добавили накануне выхода
    assert [p.step_number for p in due_steps(e, STEPS, now, sent=set())] == [1]


def test_gate_and_time_shift():
    e = emp("GTP")
    now = datetime(2026, 11, 3, 10, 1, tzinfo=MSK)
    assert 12 not in [p.step_number for p in due_steps(e, STEPS, now, sent=set())]
    assert 12 in [p.step_number for p in due_steps(e, STEPS, now, sent=set(), gates={"recruiter_go"})]
    # отмашка через неделю — шаг не устарел
    assert 12 in [p.step_number for p in due_steps(e, STEPS, now + timedelta(days=7), set(), {"recruiter_go"})]
    # машина времени: реальное 2 ноября 08:00 + 26 часов = 3 ноября 10:00
    shifted = emp("GTP", time_shift_hours=26)
    got = due_steps(shifted, STEPS, datetime(2026, 11, 2, 8, 1, tzinfo=MSK), sent={2, 3}, gates={"recruiter_go"})
    assert 12 in [p.step_number for p in got]


def test_branch_targets():
    assert [s.number for s in branch_targets(STEPS, 2, "Да!", emp("GTP"))] == [11]
    assert branch_targets(STEPS, 2, "Да!", emp("Luzhniki")) == []


@pytest.mark.skipif(not XLSX.exists(), reason="нет data/raw/korsik_scenario.xlsx")
def test_real_scenario_gtp_vs_luzhniki():
    from app.scenario.importer import parse_xlsx

    steps = parse_xlsx(XLSX)
    gtp = plan_for(emp("GTP", "production"), steps)
    luzh = plan_for(emp("Luzhniki", "office"), steps)
    g, l = {p.step_number for p in gtp}, {p.step_number for p in luzh}
    assert g != l
    by = {s.number: s for s in steps}
    assert all(not by[n].office_only for n in g)            # производству офисные шаги не идут
    assert any(by[n].office_only for n in l)
    assert all(not by[n].site_tags or "GTP" in by[n].site_tags for n in g)
    assert all(not by[n].site_tags or "Luzhniki" in by[n].site_tags for n in l)
    # «Да!» на «где пообедать» → файл своей площадки
    lunch = next(s for s in steps if any(b.action == "branch" for b in s.buttons))
    t = branch_targets(steps, lunch.number, "Да!", emp("GTP"))
    assert [s.attachment for s in t] == ["food_GTP.pdf"]
