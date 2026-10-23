"""Зоны риска: кто из новичков в адаптации требует внимания HR и почему.

`risks(session, now=None, *, site=None) -> list[RiskItem]` — собирает факты из БД одним проходом
(сигналы, обращения, ответы опросов, встречи, активность), правила — в `app.analytics.rules`.
В адаптации — новичок не в статусе finished, чей испытательный ещё не закончился (до выхода — тоже).
Опросы в карточке (пройден / не пройден / пропущен) — та же логика, что в Mini App (`EmployeeViews.surveys`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.analytics import rules as R
from app.analytics.stages import (
    adaptation_day,
    employee_today,
    first_day,
    probation_days,
    stage_of,
)
from app.db.models import (
    Consent,
    Employee,
    Flag,
    ManagerTask,
    Message,
    QuestionLog,
    Survey,
    SurveyAnswer,
    SurveyQuestion,
    Ticket,
)

log = logging.getLogger(__name__)

STAY_KEY = "fin_stay"          # «Планируешь ли ты продолжить работу по итогам испытательного срока?»
STAY_UNSURE = "Не уверен"
QUESTION_KEY = "_key"          # ключ вопроса из YAML (app.surveys.store)


@dataclass
class RiskItem:
    employee_id: int
    full_name: str
    site: str
    position: str
    day: int
    probation_days: int
    stage: str
    level: str
    reasons: list[R.Reason] = field(default_factory=list)
    connected: bool = True
    synthetic: bool = False

    @property
    def last_at(self) -> datetime | None:
        return max((r.at for r in self.reasons if r.at), default=None)

    def to_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "full_name": self.full_name,
            "site": self.site,
            "position": self.position,
            "day": self.day,
            "probation_days": self.probation_days,
            "stage": self.stage,
            "level": self.level,
            "reasons": [r.to_dict() for r in self.reasons],
            "connected": self.connected,
            "synthetic": self.synthetic,
            # карточка новичка: в Mini App — экран employee, из бота — start_param
            "card": f"/api/employees/{self.employee_id}",
            "start_param": f"employee_{self.employee_id}",
        }


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def workdays_after(due: date, today: date) -> int:
    """Сколько рабочих дней прошло после даты встречи по сегодняшний день включительно."""
    if today <= due:
        return 0
    try:
        from app.scenario.workcal import is_workday
    except Exception:  # noqa: BLE001
        def is_workday(d: date) -> bool:
            return d.weekday() < 5
    n, d = 0, due + timedelta(days=1)
    while d <= today:
        n += is_workday(d)
        d += timedelta(days=1)
    return n


async def in_adaptation(session, now: datetime, *, site: str | None = None) -> list[tuple[Employee, int, int]]:
    """[(новичок, день адаптации, длина испытательного)] — кто сейчас в адаптации, включая «до выхода»."""
    q = select(Employee).options(selectinload(Employee.site)).where(Employee.status != "finished")
    if site:
        q = q.where(Employee.site_code == site)
    out = []
    for e in (await session.scalars(q.order_by(Employee.id))).all():
        day, total = adaptation_day(e, now), probation_days(e)
        if day <= total:
            out.append((e, day, total))
    return out


def _scored(emp: Employee, code: str, answered: set) -> bool:
    """Средняя опроса учитывается, когда опрос пройден или отвечены все его шкалы (для площадки новичка).

    Шкалы пишутся по одной: посреди опроса средняя по первой оценке дала бы ложное «Оценка упала с 9 до 4».
    """
    done = (((emp.dialog_data or {}).get("surveys") or {}).get(code) or {}).get("done_at")
    if done:
        return True
    try:
        from app.surveys import config as C

        sv = C.get(code)
    except Exception:  # noqa: BLE001 — без конфига опросов судим только по отметке «пройден»
        return False
    if sv is None:
        return False
    scales = {q.key for q in sv.visible(emp.site_code) if q.kind == "scale"}
    return bool(scales) and scales <= answered


async def _question_keys(session) -> dict[int, str]:
    rows = await session.execute(select(SurveyQuestion.id, SurveyQuestion.branches))
    return {qid: (b or {}).get(QUESTION_KEY) for qid, b in rows.all()}


async def _survey_states(session, emp) -> list[R.SurveyState]:
    """Опросы как в карточке Mini App. Сломался модуль опросов — правило просто не сработает."""
    try:
        from app.api.views import EmployeeViews

        items = await EmployeeViews(session, "hr").surveys(emp)
    except Exception as e:  # noqa: BLE001
        log.warning("Опросы новичка %s для рисков: %s", emp.id, e)
        return []
    start = first_day(emp)
    return [R.SurveyState(title=s["title"], status=s["status"], on=start + timedelta(days=int(s["day"]) - 1))
            for s in items]


async def collect(session, emps: list[tuple[Employee, int, int]], now: datetime) -> dict[int, R.Facts]:
    """Факты для правил по каждому новичку."""
    from app.api.views import connected, meeting_json

    ids = [e.id for e, _, _ in emps]
    facts: dict[int, R.Facts] = {}
    for e, day, _ in emps:
        facts[e.id] = R.Facts(day=day, status=e.status, connected=connected(e))
    if not ids:
        return facts

    for eid, level, topic, status, source, created in (await session.execute(
            select(Flag.employee_id, Flag.level, Flag.topic, Flag.status, Flag.source, Flag.created_at)
            .where(Flag.employee_id.in_(ids)))).all():
        facts[eid].flags.append(R.FlagFact(level=level, topic=(topic or "").strip() or "без темы",
                                           status=status, source=source, created_at=_aware(created)))

    for eid, created in (await session.execute(
            # «Взять в работу» без ответа — тоже ожидание: как в карточке обращения (views._due)
            select(Ticket.employee_id, Ticket.created_at)
            .where(Ticket.employee_id.in_(ids), Ticket.status != "closed", Ticket.first_response_at.is_(None)))).all():
        if created is not None:
            facts[eid].tickets_waiting.append(_aware(created))

    # ответы: оценки по опросам, «Не уверен» в итоговом, последняя активность
    keys = await _question_keys(session)
    emp_by_id = {e.id: e for e, _, _ in emps}
    by_survey: dict[tuple[int, str], dict] = {}
    last: dict[int, datetime] = {}
    for eid, qid, code, title, score, value, created in (await session.execute(
            select(SurveyAnswer.employee_id, SurveyAnswer.question_id, Survey.code, Survey.title,
                   SurveyAnswer.score, SurveyAnswer.value, SurveyAnswer.created_at)
            .join(SurveyQuestion, SurveyQuestion.id == SurveyAnswer.question_id)
            .join(Survey, Survey.id == SurveyQuestion.survey_id)
            .where(SurveyAnswer.employee_id.in_(ids)))).all():
        created = _aware(created)
        if created is None:
            continue
        last[eid] = max(last.get(eid, created), created)
        if score is not None:
            s = by_survey.setdefault((eid, code), {"title": title, "scores": [], "at": created, "keys": set()})
            s["scores"].append(score)
            s["keys"].add(keys.get(qid))
            s["at"] = max(s["at"], created)
        if keys.get(qid) == STAY_KEY and (value or "").strip() == STAY_UNSURE:
            facts[eid].stay_unsure_at = created
    for (eid, code), s in sorted(by_survey.items(), key=lambda kv: kv[1]["at"]):
        if not _scored(emp_by_id[eid], code, s["keys"]):
            continue  # опрос в процессе: по первой шкале средняя ещё ничего не значит
        facts[eid].scores.append(R.SurveyScore(code=code, title=s["title"], at=s["at"],
                                               avg=sum(s["scores"]) / len(s["scores"])))

    for model, col, extra in ((Message, Message.created_at, (Message.direction == "in",)),
                              (QuestionLog, QuestionLog.created_at, ()),
                              (Consent, Consent.given_at, ())):
        rows = await session.execute(select(model.employee_id, func.max(col))
                                     .where(model.employee_id.in_(ids), *extra).group_by(model.employee_id))
        for eid, at in rows.all():
            at = _aware(at)
            if at is not None:
                last[eid] = max(last.get(eid, at), at)
    for eid, at in last.items():
        facts[eid].last_activity = at

    # встречи с руководителем: непроведённые, по «сегодня» новичка
    for t in (await session.scalars(select(ManagerTask).where(ManagerTask.employee_id.in_(ids),
                                                              ManagerTask.status != "done"))).all():
        e = emp_by_id[t.employee_id]
        if e.manager_id is not None and t.manager_id != e.manager_id:
            continue  # руководителя сменили: встреча старого руководителя не в счёт
        m = meeting_json(t, e)
        due = date.fromisoformat(m["date"])
        facts[e.id].meetings.append(R.Meeting(title=m["title"], due=due,
                                              overdue_workdays=workdays_after(due, employee_today(e, now))))

    # опросы карточки — только тем, кому бот уже пишет
    for e, day, _ in emps:
        f = facts[e.id]
        if f.connected and day >= 1:
            f.surveys = await _survey_states(session, e)
    return facts


def _sort_key(item: RiskItem):
    last = item.last_at
    return (item.level != R.HIGH, -len(item.reasons), -(last.timestamp() if last else 0), item.employee_id)


async def risks(session, now: datetime | None = None, *, site: str | None = None) -> list[RiskItem]:
    """Новички в зоне риска: сначала высокий уровень, внутри — больше причин и свежее событие."""
    from app.api.views import position_of
    from app.tickets.service import clean_name

    now = _aware(now) or datetime.now(UTC)
    emps = await in_adaptation(session, now, site=site)
    facts = await collect(session, emps, now)
    out: list[RiskItem] = []
    for e, day, total in emps:
        level, reasons = R.evaluate(facts[e.id], now)
        if level is None:
            continue
        out.append(RiskItem(
            employee_id=e.id, full_name=clean_name(e.full_name) or e.full_name, site=e.site_code,
            position=position_of(e), day=day, probation_days=total, stage=stage_of(day, total, e.status),
            level=level, reasons=reasons, connected=facts[e.id].connected, synthetic=bool(e.is_synthetic)))
    out.sort(key=_sort_key)
    return out


def reason_counts(items: list[RiskItem]) -> list[dict]:
    """Сколько новичков с каждой причиной: [{code, label, count}] по убыванию."""
    c: dict[str, int] = defaultdict(int)
    for it in items:
        for code in {r.code for r in it.reasons}:
            c[code] += 1
    order = list(R.REASON_LABELS)
    return [{"code": k, "label": R.REASON_LABELS.get(k, k), "count": n}
            for k, n in sorted(c.items(), key=lambda kv: (-kv[1], order.index(kv[0]) if kv[0] in order else 99))]
