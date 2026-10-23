"""Сводная аналитика для руководителя HR и HR: воронка этапов, настроение по площадкам и неделям,
кластеры тем, зоны риска.

Срезы настроения — по правилам еженедельной сводки (`app.reports.weekly`): цифра по площадке или неделе
только если ответили 5 и больше разных новичков, иначе «мало данных».
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.analytics.clusters import clusters as load_clusters
from app.analytics.risks import RiskItem, reason_counts, risks
from app.analytics.stages import (
    DONE_RECENT_DAYS,
    STAGES,
    adaptation_day,
    employee_today,
    probation_days,
    probation_end,
    stage_of,
)
from app.db.models import Employee, Site, SurveyAnswer
from app.reports import weekly

MOOD_WEEKS = 8
MONTHS_SHORT = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def short_range(a: date, b: date) -> str:
    """«13–19 окт», «29 сен – 5 окт»."""
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTHS_SHORT[b.month - 1]}"
    return f"{a.day} {MONTHS_SHORT[a.month - 1]} – {b.day} {MONTHS_SHORT[b.month - 1]}"


async def funnel(session, now: datetime | None = None, *, site: str | None = None,
                 risky: dict[int, str] | None = None) -> list[dict]:
    """Сколько новичков на каждом этапе сейчас. «Завершили» — испытательный закончился за 90 дней.

    risky — {id новичка: уровень риска}: сколько из них в зоне риска на этом этапе.
    """
    now = now or datetime.now(UTC)
    risky = risky or {}
    q = select(Employee).options(selectinload(Employee.site))
    if site:
        q = q.where(Employee.site_code == site)
    counts: Counter[str] = Counter()
    at_risk: Counter[str] = Counter()
    high: Counter[str] = Counter()
    for e in (await session.scalars(q)).all():
        day, total = adaptation_day(e, now), probation_days(e)
        stage = stage_of(day, total, e.status)
        if stage == "done" and (employee_today(e, now) - probation_end(e)).days > DONE_RECENT_DAYS:
            continue
        counts[stage] += 1
        if e.id in risky:
            at_risk[stage] += 1
            high[stage] += risky[e.id] == "high"
    return [{"stage": code, "label": label, "count": counts[code], "risk": at_risk[code], "risk_high": high[code]}
            for code, label in STAGES]


def _filter(raw: weekly.Raw, site: str | None) -> weekly.Raw:
    if not site:
        return raw
    return weekly.Raw(sites=[s for s in raw.sites if s[0] == site],
                      employees=[e for e in raw.employees if e["site"] == site],
                      answers=[a for a in raw.answers if a["site"] == site],
                      questions=[q for q in raw.questions if q["site"] == site],
                      flags=[f for f in raw.flags if f["site"] == site])


def _week_slice(answers: list[dict], start: date, threshold: int) -> dict:
    s = weekly._slice(answers, threshold)
    end = start + timedelta(days=6)
    shown = s["respondents"] >= threshold
    return {
        "start": start.isoformat(), "end": end.isoformat(), "label": short_range(start, end),
        "respondents": s["respondents"], "answers": s["answers"], "shown": shown,
        "avg_score": s["avg_score"] if shown else None,
        "mood": s["mood"], "mood_total": s["mood_total"], "mood_share": s["mood_share"],
    }


async def mood(session, now: datetime | None = None, *, site: str | None = None, weeks: int = MOOD_WEEKS) -> dict:
    """Настроение: по площадкам за 4 недели (как в сводке) и по календарным неделям за `weeks` недель."""
    now = now or datetime.now(UTC)
    tz = weekly._tz()
    today = now.astimezone(tz).date()
    p = weekly.period(now, week_end=today)
    report = weekly.aggregate(_filter(await weekly.collect(session, p), site), p)

    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=weeks - 1)
    by_week: dict[date, list[dict]] = {first + timedelta(weeks=i): [] for i in range(weeks)}
    lo = datetime.combine(first, time(0), tzinfo=tz)
    hi = datetime.combine(today + timedelta(days=1), time(0), tzinfo=tz)
    for a in await _answers(session, lo, hi, site):
        start = a["on"] - timedelta(days=a["on"].weekday())
        if start in by_week:
            by_week[start].append(a)
    week_rows = [_week_slice(ans, start, report["threshold"]) for start, ans in by_week.items()]
    if week_rows:
        week_rows[-1]["current"] = True
    return {
        "threshold": report["threshold"],
        "window": report["window"],
        "kpi": report["kpi"],
        "overall": {k: report["overall"][k] for k in ("respondents", "answers", "avg_score", "mood", "mood_total",
                                                       "mood_share", "shown")},
        "sites": report["mood_rows"],
        "low_data": report["low_data"],
        "weeks": week_rows,
        "synthetic": report["synthetic"],
    }


async def _answers(session, lo: datetime, hi: datetime, site: str | None) -> list[dict]:
    """Ответы опросов в форме weekly.collect плюс день ответа по Москве («on»)."""
    tz = weekly._tz()
    q = (select(SurveyAnswer.employee_id, Employee.site_code, SurveyAnswer.score, SurveyAnswer.mood,
                SurveyAnswer.topic, SurveyAnswer.is_synthetic, Employee.is_synthetic, SurveyAnswer.created_at)
         .join(Employee, Employee.id == SurveyAnswer.employee_id)
         .where(SurveyAnswer.created_at >= lo, SurveyAnswer.created_at < hi))
    if site:
        q = q.where(Employee.site_code == site)
    return [{"employee_id": r[0], "site": r[1], "score": r[2], "mood": r[3], "topic": r[4],
             "synthetic": bool(r[5] or r[6]), "on": r[7].astimezone(tz).date()}
            for r in (await session.execute(q)).all()]


async def summary(session, now: datetime | None = None, *, site: str | None = None, weeks: int = 4,
                  items: list[RiskItem] | None = None) -> dict:
    """GET /api/analytics: воронка, настроение по площадкам и неделям, кластеры, число рисков."""
    now = now or datetime.now(UTC)
    if items is None:
        items = await risks(session, now, site=site)
    risky = {i.employee_id: i.level for i in items}
    names = dict((await session.execute(select(Site.code, Site.name))).all())
    cl = await load_clusters(session, weeks, now, site=site)
    for c in cl:
        for s in c["sites"]:
            s["name"] = names.get(s["code"], s["code"])
        for q in c["quotes"]:
            q["site_name"] = names.get(q["site"], q["site"])
    m = await mood(session, now, site=site)
    return {
        "generated_at": now.isoformat(),
        "site": site,
        "weeks": weeks,
        "window": m["window"],
        "threshold": m["threshold"],
        "kpi": {**m["kpi"], "risks": len(items), "risks_high": sum(1 for i in items if i.level == "high")},
        "funnel": await funnel(session, now, site=site, risky=risky),
        "mood": {k: m[k] for k in ("overall", "sites", "low_data", "weeks")},
        "clusters": cl,
        "risks": {
            "total": len(items),
            "high": sum(1 for i in items if i.level == "high"),
            "medium": sum(1 for i in items if i.level == "medium"),
            "reasons": reason_counts(items),
        },
        "sites": [{"code": c, "name": n} for c, n in sorted(names.items())],
        "synthetic": m["synthetic"] or any(i.synthetic for i in items),
    }
