"""JSON для Mini App: формы из miniapp/src/api/types.ts. Данные — из БД и модулей по app/CONTRACTS.md."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import call, optional
from app.config import settings
from app.db.models import (
    Delivery,
    Employee,
    Flag,
    HrAnswer,
    KbChunk,
    KbDocument,
    ManagerTask,
    Message,
    QuestionLog,
    ScenarioStep,
    Staff,
    Survey,
    SurveyAnswer,
    SurveyQuestion,
    Ticket,
)
from app.tickets import service as tickets

log = logging.getLogger(__name__)

RED_DUE = timedelta(hours=1)       # красный флаг: связаться в течение часа
TICKET_DUE = timedelta(hours=24)   # остальное: в течение рабочего дня
SURVEY_MISSED_AFTER = timedelta(days=3)
CONTEXT_WINDOW = timedelta(minutes=20)
CONTEXT_LIMIT = 4
MEETING_TITLES = {3: "Встреча на 3-й день", 30: "Встреча на 30-й день", 60: "Встреча на 60-й день"}
FINAL_MEETING = "Итоговая встреча"
ROLE_LABELS = {"hr": "HR", "recruiter": "Рекрутер", "manager": "Руководитель", "head_recruitment": "Глава подбора"}
_SOURCE_RE = re.compile(r"^\s*Источник:\s*(.+?)\s*$", re.MULTILINE)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _tz(emp) -> ZoneInfo:
    site = vars(emp).get("site")
    return ZoneInfo(getattr(site, "tz", None) or settings.tz_default)


def _first_day(emp):
    """День выхода как у движка: первый рабочий день с даты выхода."""
    fn = optional("app.scenario.engine", "first_day")
    if fn is not None:
        try:
            return fn(emp)
        except Exception as e:  # noqa: BLE001
            log.debug("engine.first_day: %s", e)
    return emp.start_date


def _day_of(emp, when: datetime) -> int:
    """День адаптации для момента времени (по часовому поясу площадки)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (when.astimezone(_tz(emp)).date() - _first_day(emp)).days + 1


def invite_link(code: str) -> str:
    bot = (settings.max_bot_name or "").lstrip("@") or "korsik_bot"
    return f"https://max.ru/{bot}?start={code}"


def position_of(emp) -> str:
    pos = (emp.dialog_data or {}).get("position")
    if pos:
        return str(pos)
    return "Сотрудник производства" if emp.work_type == "production" else "Сотрудник офиса"


def contact(staff: Staff | None, role: str, fallback_id: int) -> dict:
    if staff is None:
        return {"id": fallback_id, "name": "Не назначен", "role": ROLE_LABELS.get(role, role), "phone": None}
    label = ROLE_LABELS.get(staff.role, staff.role)
    if staff.role == "manager" and staff.site_code:
        label = f"{label}, {staff.site_code}"
    return {"id": staff.id, "name": tickets.clean_name(staff.full_name), "role": label, "phone": None}


def step_title(step: ScenarioStep) -> str:
    title = (step.topic or "").strip()
    if not title:
        first = (step.text or "").strip().splitlines()
        title = first[0] if first else f"Шаг {step.step_no or step.id}"
    return title if len(title) <= 80 else title[:79].rstrip() + "…"


def survey_code(step: ScenarioStep) -> str | None:
    """Код опроса для шага-опроса: surveys.engine.survey_for_step, иначе карта шагов из config/surveys.yaml."""
    keys = [k for k in (step.order_no, step.step_no) if k not in (None, "")]
    for_step = optional("app.surveys.engine", "survey_for_step")
    if for_step is not None:
        for k in keys:
            try:
                code = for_step(k)
            except Exception:  # noqa: BLE001
                code = None
            if code:
                return code
        return None
    step_map = optional("app.surveys.config", "step_map")
    if step_map is None:
        return None
    try:
        m = step_map()
    except Exception:  # noqa: BLE001
        return None
    for k in keys:
        code = m.get(k) or (m.get(int(k)) if str(k).isdigit() else None)
        if code:
            return code
    return None


def meeting_json(task: ManagerTask, emp) -> dict:
    fn = optional("app.manager.service", "meeting_dict")
    if fn is not None:
        try:
            m = fn(task)
            if m.get("date") is None:
                m["date"] = (emp.start_date + timedelta(days=task.day - 1)).isoformat()
            return m
        except Exception as e:  # noqa: BLE001
            log.debug("manager.meeting_dict: %s", e)
    title = MEETING_TITLES.get(task.day, FINAL_MEETING) if task.kind == "meeting" else FINAL_MEETING
    due = task.due_date or emp.start_date + timedelta(days=task.day - 1)
    return {"id": task.id, "day": task.day, "title": title, "date": due.isoformat(), "done": task.status == "done"}


def gates_of(emp) -> dict:
    """Отмашки рекрутера: из движка сценария, иначе из dialog_data["gates"]."""
    fn = optional("app.scenario.engine", "gates")
    if fn is not None:
        try:
            g = fn(emp)
            return {"recruiter_go": bool(g.get("recruiter_go")), "plan_ready": bool(g.get("plan_ready"))}
        except Exception as e:  # noqa: BLE001
            log.debug("engine.gates: %s", e)
    raw = (emp.dialog_data or {}).get("gates") or {}
    return {"recruiter_go": bool(raw.get("recruiter_go")), "plan_ready": bool(raw.get("plan_ready"))}


def intro_of(emp) -> dict | None:
    intro = (emp.dialog_data or {}).get("intro")
    if not isinstance(intro, dict) or not intro.get("text"):
        return None
    return {"text": intro.get("text"), "photo_url": intro.get("photo_url"), "at": intro.get("at")}


# --- тикеты ---------------------------------------------------------------------

async def _ticket_meta(session: AsyncSession, ids: list[int]) -> tuple[dict[int, set[str]], dict[int, str]]:
    """Уровни флагов и темы вопросов по обращениям."""
    levels: dict[int, set[str]] = defaultdict(set)
    topics: dict[int, str] = {}
    if not ids:
        return levels, topics
    for tid, level in (await session.execute(
            select(Flag.ticket_id, Flag.level).where(Flag.ticket_id.in_(ids)))).all():
        levels[tid].add(level)
    for tid, topic in (await session.execute(
            select(QuestionLog.ticket_id, QuestionLog.topic)
            .where(QuestionLog.ticket_id.in_(ids), QuestionLog.topic.is_not(None)))).all():
        topics.setdefault(tid, topic)
    return levels, topics


def _due(t: Ticket, red: bool) -> str | None:
    if t.status == "closed" or t.first_response_at is not None:
        return None
    created = t.created_at or datetime.now(UTC)
    return iso(created + (RED_DUE if red else TICKET_DUE))


def ticket_short_json(t: Ticket, emp: Employee, levels: set[str], topic: str | None) -> dict:
    red = tickets.is_red(t, levels)
    first_line = next((ln.strip() for ln in (t.text or "").splitlines() if ln.strip()), "")
    return {
        "id": t.id,
        "employee_id": emp.id,
        "employee_name": tickets.clean_name(emp.full_name),
        "site": emp.site_code,
        "day": tickets.employee_day(emp),
        "category": tickets.category_of(t),
        "category_label": tickets.category_label(t, topic),
        "red_flag": red,
        "status": tickets.API_STATUS.get(t.status, t.status),
        "preview": first_line[:200],
        "created_at": iso(t.created_at),
        "due_at": _due(t, red),
    }


async def ticket_shorts(session: AsyncSession, items: list[Ticket]) -> list[dict]:
    if not items:
        return []
    emp_ids = {t.employee_id for t in items}
    emps = {e.id: e for e in await session.scalars(select(Employee).where(Employee.id.in_(emp_ids)))}
    levels, topics = await _ticket_meta(session, [t.id for t in items])
    return [ticket_short_json(t, emps[t.employee_id], levels.get(t.id, set()), topics.get(t.id)) for t in items]


def sort_tickets(rows: list[dict]) -> list[dict]:
    """Сначала красные флаги, внутри — новые сверху."""
    rows = sorted(rows, key=lambda r: r["created_at"] or "", reverse=True)
    return sorted(rows, key=lambda r: not r["red_flag"])


def _message_json(m: Message, emp: Employee, staff: dict[int, Staff]) -> dict:
    text = m.text or ""
    source = None
    if m.direction == "in":
        author, name = "employee", tickets.clean_name(emp.full_name)
    elif m.staff_id and m.staff_id in staff:
        author, name = "hr", tickets.clean_name(staff[m.staff_id].full_name)
    else:
        author, name = "bot", "Корсик"
        found = _SOURCE_RE.search(text)
        if found:
            source = found.group(1)
            text = _SOURCE_RE.sub("", text).strip()
    return {"id": m.id, "author": author, "author_name": name, "text": text, "created_at": iso(m.created_at),
            "source": source}


async def ticket_messages(session: AsyncSession, t: Ticket, emp: Employee) -> list[dict]:
    """Лента обращения: пара сообщений до него (контекст) + всё, что попало в обращение."""
    own = list(await session.scalars(
        select(Message).where(Message.ticket_id == t.id, Message.employee_id == emp.id)
        .order_by(Message.created_at, Message.id)))
    context: list[Message] = []
    if t.created_at is not None:
        context = list(await session.scalars(
            select(Message).where(Message.employee_id == emp.id, Message.ticket_id.is_(None),
                                  Message.created_at >= t.created_at - CONTEXT_WINDOW,
                                  Message.created_at <= t.created_at)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(CONTEXT_LIMIT)))
    rows = [m for m in [*reversed(context), *own] if (m.text or "").strip() and not m.text.startswith("[кнопка]")]
    staff_ids = {m.staff_id for m in rows if m.staff_id}
    staff = {s.id: s for s in await session.scalars(select(Staff).where(Staff.id.in_(staff_ids)))} \
        if staff_ids else {}
    out = [_message_json(m, emp, staff) for m in rows]
    quote = (t.text or "").strip()
    if quote and not any(m["author"] == "employee" and m["text"].strip() == quote for m in out):
        # обращение из опроса или флага: цитата новичка — первым сообщением
        out.append({"id": -t.id, "author": "employee", "author_name": tickets.clean_name(emp.full_name),
                    "text": quote, "created_at": iso(t.created_at), "source": None})
    out.sort(key=lambda m: (m["created_at"] or "", max(0, m["id"])))
    return out


async def ticket_json(session: AsyncSession, t: Ticket) -> dict:
    emp = await session.get(Employee, t.employee_id)
    levels, topics = await _ticket_meta(session, [t.id])
    data = ticket_short_json(t, emp, levels.get(t.id, set()), topics.get(t.id))
    assignee = await session.get(Staff, t.assignee_id) if t.assignee_id else None
    data["assignee"] = tickets.short_name(assignee.full_name) if assignee else None
    data["messages"] = await ticket_messages(session, t, emp)
    data["is_personal"] = bool(t.is_personal)
    return data


# --- новички -------------------------------------------------------------------------

class EmployeeViews:
    """Сборка EmployeeShort / Employee. Тикеты, флаги, встречи и персонал — одним запросом на список."""

    def __init__(self, session: AsyncSession, role: str):
        self.s = session
        self.role = role
        self.hr = role != "manager"
        self.tickets: dict[int, list[Ticket]] = defaultdict(list)
        self.levels: dict[int, set[str]] = {}
        self.topics: dict[int, str] = {}
        self.red_emps: set[int] = set()
        self.tasks: dict[int, list[ManagerTask]] = defaultdict(list)
        self.staff: dict[int, Staff] = {}

    async def prepare(self, emps: list[Employee]) -> None:
        ids = [e.id for e in emps]
        if not ids:
            return
        self.staff = {s.id: s for s in await self.s.scalars(select(Staff))}
        for t in await self.s.scalars(select(ManagerTask).where(ManagerTask.employee_id.in_(ids))
                                      .order_by(ManagerTask.due_date, ManagerTask.day, ManagerTask.id)):
            self.tasks[t.employee_id].append(t)
        if not self.hr:
            return
        all_t = list(await self.s.scalars(select(Ticket).where(Ticket.employee_id.in_(ids))
                                          .order_by(Ticket.created_at.desc())))
        for t in all_t:
            self.tickets[t.employee_id].append(t)
        self.levels, self.topics = await _ticket_meta(self.s, [t.id for t in all_t])
        self.red_emps = set(await self.s.scalars(
            select(Flag.employee_id).where(Flag.employee_id.in_(ids), Flag.level == "red", Flag.status != "resolved")))

    def meetings(self, emp) -> list[dict]:
        return [meeting_json(t, emp) for t in self.tasks.get(emp.id, [])]

    async def upcoming(self, emp, limit: int = 5) -> list[dict]:
        fn = optional("app.scenario.engine", "upcoming_steps")
        if fn is None:
            return []
        try:
            return list(await call(fn, self.s, emp, limit=limit) or [])
        except Exception as e:  # noqa: BLE001 — шаги не должны ронять карточку
            log.warning("engine.upcoming_steps(%s): %s", emp.id, e)
            return []

    async def surveys(self, emp) -> list[dict]:
        """Опросы новичка: отправленные шаги-опросы и ответы из survey_answers."""
        answers: dict[int, dict] = {}
        for sid, score, created in (await self.s.execute(
                select(SurveyQuestion.survey_id, SurveyAnswer.score, SurveyAnswer.created_at)
                .join(SurveyQuestion, SurveyAnswer.question_id == SurveyQuestion.id)
                .where(SurveyAnswer.employee_id == emp.id))).all():
            a = answers.setdefault(sid, {"scores": [], "at": created})
            if score is not None:
                a["scores"].append(score)
            if created and (a["at"] is None or created < a["at"]):
                a["at"] = created

        sent: dict[int, datetime] = {}
        rows = (await self.s.execute(
            select(Delivery.scheduled_at, Delivery.sent_at, ScenarioStep)
            .join(ScenarioStep, Delivery.step_id == ScenarioStep.id)
            .where(Delivery.employee_id == emp.id, Delivery.status == "sent", ScenarioStep.kind == "survey"))).all()
        if rows:
            codes: dict[str, int] = {c: i for i, c in (await self.s.execute(select(Survey.id, Survey.code))).all()}
            for scheduled, sent_at, step in rows:
                sid = step.survey_id
                if sid is None:
                    code = survey_code(step)
                    sid = codes.get(code) if code else None
                if sid is not None:
                    when = sent_at or scheduled
                    sent[sid] = min(sent.get(sid, when), when)

        ids = set(answers) | set(sent)
        if not ids:
            return []
        titles = {s.id: s.title for s in await self.s.scalars(select(Survey).where(Survey.id.in_(ids)))}
        now = datetime.now(UTC)
        items = []
        for sid in ids:
            a = answers.get(sid)
            when = sent.get(sid) or (a or {}).get("at") or now
            if a:
                scores = a["scores"]
                status, score = "done", (round(sum(scores) / len(scores)) if scores else None)
            else:
                status, score = ("missed" if now - when > SURVEY_MISSED_AFTER else "pending"), None
            items.append({"id": sid, "title": titles.get(sid, "Опрос"), "day": _day_of(emp, when),
                          "status": status, "score": score, "_at": when})
        items.sort(key=lambda x: x["_at"])
        for x in items:
            x.pop("_at")
        return items

    async def steps(self, emp, upcoming: list[dict]) -> list[dict]:
        done = (await self.s.execute(
            select(Delivery.scheduled_at, ScenarioStep)
            .join(ScenarioStep, Delivery.step_id == ScenarioStep.id)
            .where(Delivery.employee_id == emp.id, Delivery.status == "sent")
            .order_by(Delivery.scheduled_at))).all()
        out = []
        for when, step in done:
            out.append({"day": _day_of(emp, when), "title": step_title(step),
                        "date": when.astimezone(_tz(emp)).date().isoformat(), "status": "done"})
        for i, u in enumerate(upcoming):
            try:
                at = datetime.fromisoformat(str(u.get("at")))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=_tz(emp))
            out.append({"day": _day_of(emp, at), "title": str(u.get("title") or "Шаг сценария"),
                        "date": at.astimezone(_tz(emp)).date().isoformat(),
                        "status": "current" if i == 0 else "upcoming"})
        return out

    async def short(self, emp, *, upcoming: list[dict] | None = None, surveys: list[dict] | None = None) -> dict:
        if upcoming is None:
            upcoming = await self.upcoming(emp, limit=1)
        if surveys is None:
            surveys = await self.surveys(emp) if self.hr else []
        own = self.tickets.get(emp.id, []) if self.hr else []
        open_t = [t for t in own if t.status != "closed"]
        red = self.hr and (emp.id in self.red_emps or any(tickets.is_red(t, self.levels.get(t.id, set()))
                                                          for t in open_t))
        meetings = self.meetings(emp)
        return {
            "id": emp.id,
            "full_name": tickets.clean_name(emp.full_name) or emp.full_name,
            "position": position_of(emp),
            "site": emp.site_code,
            "work_type": emp.work_type,
            "start_date": emp.start_date.isoformat(),
            "day": tickets.employee_day(emp),
            "probation_days": tickets.probation_days(emp),
            "current_step": str(upcoming[0].get("title")) if upcoming else None,
            "pending_surveys": sum(1 for s in surveys if s["status"] != "done"),
            "open_tickets": len(open_t),
            "red_flag": bool(red),
            "connected": emp.max_user_id is not None,
            "next_meeting": next((m for m in meetings if not m["done"]), None),
            "synthetic": bool(emp.is_synthetic),
        }

    async def full(self, emp) -> dict:
        upcoming = await self.upcoming(emp, limit=5)
        surveys = await self.surveys(emp) if self.hr else []
        data = await self.short(emp, upcoming=upcoming, surveys=surveys)
        intro = intro_of(emp)
        own = self.tickets.get(emp.id, []) if self.hr else []
        data.update({
            "manager": contact(self.staff.get(emp.manager_id), "manager", -1),
            "recruiter": contact(self.staff.get(emp.recruiter_id), "recruiter", -2),
            "intro_text": intro["text"] if intro else None,
            "intro": intro,
            "steps": await self.steps(emp, upcoming),
            "surveys": surveys,
            "tickets": [ticket_short_json(t, emp, self.levels.get(t.id, set()), self.topics.get(t.id))
                        for t in own],
            "meetings": self.meetings(emp),
            "invite_link": invite_link(emp.invite_code),
            "gates": gates_of(emp),
            "status": emp.status,
        })
        return data


# --- база знаний ---------------------------------------------------------------------

async def kb_documents(session: AsyncSession) -> list[dict]:
    counts = dict((await session.execute(
        select(KbChunk.document_id, func.count()).where(KbChunk.document_id.is_not(None))
        .group_by(KbChunk.document_id))).all())
    out = [document_json(d, counts.get(d.id, 0))
           for d in await session.scalars(select(KbDocument).order_by(KbDocument.created_at.desc(),
                                                                       KbDocument.id.desc()))]
    n_steps, last = (await session.execute(
        select(func.count(), func.max(ScenarioStep.created_at)).where(ScenarioStep.is_active))).one()
    if n_steps:
        out.append({"id": 0, "title": f"Сценарий адаптации «Корсик», шагов: {n_steps}", "kind": "xlsx",
                    "trade_secret": False, "status": "confirmed",
                    "version_date": (last or datetime.now(UTC)).date().isoformat(),
                    "fragments": n_steps, "chunks": n_steps, "site": None})
    answer_chunks = dict((await session.execute(
        select(KbChunk.hr_answer_id, func.count()).where(KbChunk.hr_answer_id.is_not(None))
        .group_by(KbChunk.hr_answer_id))).all())
    for a in await session.scalars(select(HrAnswer).order_by(HrAnswer.created_at.desc(), HrAnswer.id.desc())):
        out.append(answer_json(a, answer_chunks.get(a.id, 0)))
    return out


def doc_kind(d: KbDocument) -> str:
    """Тип для иконки Mini App: pdf | pptx | xlsx. Документы из docs/*.md — по формату оригинала."""
    if d.doc_type in ("pdf", "pptx", "xlsx"):
        return d.doc_type
    source = d.source or ""
    if source.startswith("scenario:") or d.doc_type in ("scenario", "article"):
        return "xlsx"  # справочные статьи из сценария
    docs = optional("app.kb.chunker", "DOCS") or {}
    meta = docs.get(source.rsplit("/", 1)[-1].removesuffix(".md"))
    return "pptx" if meta and meta[1] == "slides" else "pdf"


def document_json(d: KbDocument, chunks: int) -> dict:
    sites = d.site_codes or []
    return {"id": d.id, "title": d.title, "kind": doc_kind(d), "doc_type": d.doc_type, "trade_secret": bool(d.is_kt), "status": d.status,
            "version_date": (d.created_at or datetime.now(UTC)).date().isoformat(), "fragments": chunks,
            "chunks": chunks, "site": sites[0] if len(sites) == 1 else None, "sites": sites}


def answer_json(a: HrAnswer, chunks: int = 0) -> dict:
    title = " ".join((a.question or "").split())
    if len(title) > 90:
        title = title[:89].rstrip() + "…"
    return {"id": a.id, "title": title or "Ответ HR", "kind": "answer", "trade_secret": False, "status": a.status,
            "version_date": (a.created_at or datetime.now(UTC)).date().isoformat(), "fragments": chunks,
            "chunks": chunks, "site": a.site_code, "question": a.question, "answer": a.answer,
            "ticket_id": a.ticket_id}


# --- частые темы ------------------------------------------------------------------------

ROUTE_TOPICS = {"personal": "Личные вопросы", "unknown": "Нет ответа в базе", "hr": "Вопросы к HR"}


def _peak(days: list[int]) -> str:
    """Где сгусток вопросов: середина распределения дней адаптации."""
    days = sorted(days)
    lo, hi = days[(len(days) - 1) // 4], days[(3 * (len(days) - 1) + 3) // 4]
    if hi < 1:
        return "до выхода"
    lo = max(lo, 1)
    return f"день {lo}" if lo == hi else f"дни {lo}–{hi}"


def _topic_from_path(path: str | None) -> str | None:
    if not path:
        return None
    head = re.split(r"\s*(?:→|>|/)\s*", path.strip())[0]
    head = re.sub(r"^(?:раздел|глава|слайд)?\s*[\d.]+\s*[.)]?\s*", "", head, flags=re.IGNORECASE).strip()
    return head[:48] or None


async def topics(session: AsyncSession, *, weeks: int = 4) -> list[dict]:
    since = datetime.now(UTC) - timedelta(weeks=weeks)
    rows = (await session.execute(
        select(QuestionLog.topic, QuestionLog.route, QuestionLog.created_at, KbChunk.path,
               Employee.start_date, Employee.site_code)
        .join(Employee, QuestionLog.employee_id == Employee.id)
        .outerjoin(KbChunk, QuestionLog.chunk_id == KbChunk.id)
        .where(QuestionLog.created_at >= since))).all()
    groups: dict[str, list[int]] = defaultdict(list)
    tz = ZoneInfo(settings.tz_default)
    for topic, route, created, path, start, _site in rows:
        label = (topic or "").strip() or ROUTE_TOPICS.get(route) or _topic_from_path(path) or "Другое"
        label = label[:1].upper() + label[1:]
        groups[label].append((created.astimezone(tz).date() - start).days + 1)
    out = [{"topic": k, "count": len(v), "peak_days": _peak(v)} for k, v in groups.items()]
    out.sort(key=lambda x: (-x["count"], x["topic"]))
    return out[:12]
