"""JSON для Mini App: формы из miniapp/src/api/types.ts. Данные — из БД и модулей по app/CONTRACTS.md."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
ROLE_LABELS = {"hr": "HR", "recruiter": "Рекрутер", "manager": "Руководитель", "head_recruitment": "Руководитель HR"}
_SOURCE_RE = re.compile(r"^\s*Источник:\s*(.+?)\s*$", re.MULTILINE)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHOTO_ROOTS = (PROJECT_ROOT / "data", PROJECT_ROOT / "assets")  # откуда можно отдавать фото файлом
PHOTO_TTL = 24 * 3600  # подписанная ссылка на фото живёт сутки: <img> не умеет слать заголовок initData
_PHOTO_FALLBACK_KEY = secrets.token_bytes(32)


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


def survey_code(step: ScenarioStep) -> str | None:
    """Код опроса для шага сценария, как у движка: surveys.engine.survey_for_step(номер строки).

    Тип шага не важен: шаги 20, 21 и 47 в xlsx — «message», но это опросы.
    """
    if step.order_no is None:
        return None
    for_step = optional("app.surveys.engine", "survey_for_step")
    if for_step is None:
        return None
    try:
        return for_step(step.order_no) or None
    except Exception:  # noqa: BLE001
        return None


def survey_steps() -> list[int]:
    """Номера шагов сценария, которые покрывают опросы (config/surveys.yaml)."""
    step_map = optional("app.surveys.config", "step_map")
    try:
        return sorted(step_map()) if step_map is not None else []
    except Exception:  # noqa: BLE001
        return []


def connected(emp) -> bool:
    """Подключился ли новичок к боту. Синтетические новички в статусе active — «подключились» (демо)."""
    return emp.max_user_id is not None or (bool(emp.is_synthetic) and emp.status == "active")


def employee_now(emp, now: datetime | None = None) -> datetime:
    """«Сейчас» для новичка с учётом машины времени."""
    return (now or datetime.now(UTC)) + timedelta(hours=emp.time_shift_hours or 0)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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


def is_external_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def _photo_key() -> bytes:
    token = settings.max_bot_token
    return hashlib.sha256(b"intro-photo:" + token.encode()).digest() if token else _PHOTO_FALLBACK_KEY


def photo_sig(emp_id: int, exp: int) -> str:
    return hmac.new(_photo_key(), f"{emp_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def check_photo_sig(emp_id: int, exp: int, sig: str, now: float | None = None) -> bool:
    if not sig or exp < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(photo_sig(emp_id, exp), sig)


def intro_photo_url(emp, photo: str | None, now: float | None = None) -> str | None:
    """Ссылка на фото «представления коллегам» для Mini App.

    Внешний https-адрес фото «МАКС» — как есть. Файл или токен «МАКС» — через API
    (GET /api/employees/{id}/intro-photo) с подписью в адресе: <img> не передаёт заголовок initData,
    а карточку с этой ссылкой уже выдали тому, кому её можно видеть.
    """
    if not photo:
        return None
    photo = str(photo).strip()
    if is_external_url(photo):
        return photo
    exp = int(time.time() if now is None else now) + PHOTO_TTL
    v = hashlib.sha256(photo.encode()).hexdigest()[:8]  # другое фото — другой адрес, кэш браузера не мешает
    return f"/api/employees/{emp.id}/intro-photo?v={v}&exp={exp}&sig={photo_sig(emp.id, exp)}"


def intro_photo_file(photo: str | None) -> Path | None:
    """Фото лежит файлом в data/ или assets/ проекта. Иначе (токен «МАКС», чужой путь) — None."""
    if not photo or is_external_url(photo):
        return None
    try:
        path = Path(photo)
        path = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()
    except (OSError, ValueError):
        return None
    if not any(path.is_relative_to(root.resolve()) for root in PHOTO_ROOTS):
        return None
    return path if path.is_file() else None


def intro_of(emp) -> dict | None:
    intro = (emp.dialog_data or {}).get("intro")
    if not isinstance(intro, dict) or not intro.get("text"):
        return None
    return {"text": intro.get("text"), "photo_url": intro_photo_url(emp, intro.get("photo_url")),
            "at": intro.get("at")}


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
    data["synthetic"] = bool(emp.is_synthetic)  # демо-новичок: в «МАКС» ему ничего не уходит
    return data


# --- новички -------------------------------------------------------------------------

def current_index(steps: list[dict], today: str) -> int | None:
    """Шаг «сейчас»: последний пройденный сегодня, иначе первый сегодняшний из ближайших."""
    done = [i for i, s in enumerate(steps) if s["status"] == "done" and s["date"] == today]
    if done:
        return done[-1]
    return next((i for i, s in enumerate(steps) if s["status"] == "upcoming" and s["date"] == today), None)


def current_step(steps: list[dict]) -> str | None:
    """Подпись в списке новичков: шаг сегодняшнего дня, иначе ближайший."""
    cur = next((s for s in steps if s["status"] == "current"), None) \
        or next((s for s in steps if s["status"] == "upcoming"), None)
    return cur["title"] if cur else None


class EmployeeViews:
    """Сборка EmployeeShort / Employee. Тикеты, флаги, встречи, персонал и сценарий — одним запросом на список."""

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
        self.plan = None  # сценарий движка, один на все карточки
        self.survey_rows: dict[str, Survey] | None = None

    async def prepare(self, emps: list[Employee]) -> None:
        ids = [e.id for e in emps]
        if not ids:
            return
        self.staff = {s.id: s for s in await self.s.scalars(select(Staff))}
        for t in await self.s.scalars(select(ManagerTask).where(ManagerTask.employee_id.in_(ids))
                                      .order_by(ManagerTask.due_date, ManagerTask.day, ManagerTask.id)):
            self.tasks[t.employee_id].append(t)
        load_plan = optional("app.scenario.engine", "load_plan")
        if load_plan is not None:
            try:
                self.plan = await call(load_plan, self.s)
            except Exception as e:  # noqa: BLE001 — без сценария карточка всё равно открывается
                log.warning("engine.load_plan: %s", e)
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

    async def timeline(self, emp, limit: int = 5) -> dict:
        """{"past", "upcoming", "now"} из движка сценария (engine.timeline); без него — только ближайшие шаги."""
        try:
            fn = optional("app.scenario.engine", "timeline")
            if fn is not None:
                return dict(await call(fn, self.s, emp, limit=limit, plan=self.plan) or {})
            fn = optional("app.scenario.engine", "upcoming_steps")
            if fn is not None:
                return {"past": [], "upcoming": list(await call(fn, self.s, emp, limit=limit) or [])}
        except Exception as e:  # noqa: BLE001 — шаги не должны ронять карточку
            log.warning("engine.timeline(%s): %s", emp.id, e)
        return {"past": [], "upcoming": []}

    def steps(self, emp, tl: dict) -> list[dict]:
        """Шаги карточки: пройденные (done), сегодняшний (current), ближайшие (upcoming). Форма Step из types.ts."""
        tz = _tz(emp)
        now = _parse_dt(tl.get("now")) or employee_now(emp)
        out = []
        for status, items in (("done", tl.get("past") or []), ("upcoming", tl.get("upcoming") or [])):
            for item in items:
                at = _parse_dt(item.get("at"))
                if at is None:
                    continue
                step = {"day": _day_of(emp, at), "title": str(item.get("title") or "Сообщение Корсика"),
                        "date": at.astimezone(tz).date().isoformat(), "status": status}
                if out and all(out[-1][k] == step[k] for k in ("title", "date", "status")):
                    continue  # два момента одного шага в один день («Опрос двух месяцев» в 10:00 и 10:05)
                out.append(step)
        cur = current_index(out, now.astimezone(tz).date().isoformat())
        if cur is not None:
            out[cur]["status"] = "current"
        return out

    async def _survey_rows(self) -> dict[str, Survey]:
        if self.survey_rows is None:
            self.survey_rows = {s.code: s for s in await self.s.scalars(select(Survey))}
        return self.survey_rows

    async def surveys(self, emp) -> list[dict]:
        """Опросы новичка: отправленные шаги-опросы, начатые опросы и ответы.

        - пройден — есть survey_history(emp)[код]["done_at"]; у синтетики истории нет, тогда — по ответам;
        - не пройден — pending_surveys(emp) или отправлен без done_at; старше 3 дней или закрыт — «пропущен».
        Шаг-опрос — по survey_for_step(номер строки), не по типу шага.
        """
        history: dict = {}
        pending: set[str] = set()
        fn = optional("app.surveys.engine", "survey_history")
        if fn is not None:
            try:
                history = dict(fn(emp) or {})
            except Exception as e:  # noqa: BLE001
                log.debug("surveys.survey_history: %s", e)
        fn = optional("app.surveys.engine", "pending_surveys")
        if fn is not None:
            try:
                pending = set(fn(emp) or [])
            except Exception as e:  # noqa: BLE001
                log.debug("surveys.pending_surveys: %s", e)

        rows = await self._survey_rows()
        code_by_id = {s.id: code for code, s in rows.items()}
        answers: dict[str, dict] = {}
        for sid, score, created in (await self.s.execute(
                select(SurveyQuestion.survey_id, SurveyAnswer.score, SurveyAnswer.created_at)
                .join(SurveyQuestion, SurveyAnswer.question_id == SurveyQuestion.id)
                .where(SurveyAnswer.employee_id == emp.id))).all():
            code = code_by_id.get(sid)
            if code is None:
                continue
            a = answers.setdefault(code, {"scores": [], "at": created})
            if score is not None:
                a["scores"].append(score)
            if created and (a["at"] is None or created < a["at"]):
                a["at"] = created

        sent: dict[str, datetime] = {}
        numbers = survey_steps()
        if numbers:
            for scheduled, sent_at, step in (await self.s.execute(
                    select(Delivery.scheduled_at, Delivery.sent_at, ScenarioStep)
                    .join(ScenarioStep, Delivery.step_id == ScenarioStep.id)
                    .where(Delivery.employee_id == emp.id, Delivery.status == "sent",
                           ScenarioStep.order_no.in_(numbers)))).all():
                code = survey_code(step)
                when = scheduled or sent_at
                if code and when:
                    sent[code] = min(sent.get(code, when), when)

        now = employee_now(emp)
        items = []
        for code in set(history) | pending | set(sent) | set(answers):
            row = rows.get(code)
            if row is None:
                continue  # опроса ещё нет в БД (store.sync не запускали)
            h = history.get(code) if isinstance(history.get(code), dict) else None
            a = answers.get(code)
            when = sent.get(code) or _parse_dt((h or {}).get("started_at")) or (a or {}).get("at") or now
            if code in pending:
                done = False
            elif h is not None:
                done = bool(h.get("done_at"))
            else:
                done = a is not None  # синтетика: истории опросов нет, только ответы
            if done:
                scores = (a or {}).get("scores") or []
                status, score = "done", (round(sum(scores) / len(scores)) if scores else None)
            else:
                stale = bool((h or {}).get("closed_at")) or now - when > SURVEY_MISSED_AFTER
                status, score = ("missed" if stale else "pending"), None
            items.append({"id": row.id, "title": row.title, "day": _day_of(emp, when), "status": status,
                          "score": score, "_at": when})
        items.sort(key=lambda x: x["_at"])
        for x in items:
            x.pop("_at")
        return items

    async def short(self, emp, *, tl: dict | None = None, surveys: list[dict] | None = None) -> dict:
        if tl is None:
            tl = await self.timeline(emp, limit=1)
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
            # день выхода как у движка: HR указал выходной — первый день в понедельник, от него и «день N»
            "first_day": _first_day(emp).isoformat(),
            "day": tickets.employee_day(emp),
            "probation_days": tickets.probation_days(emp),
            "current_step": current_step(self.steps(emp, tl)),
            "pending_surveys": sum(1 for s in surveys if s["status"] != "done"),
            "open_tickets": len(open_t),
            "red_flag": bool(red),
            "connected": connected(emp),
            "next_meeting": next((m for m in meetings if not m["done"]), None),
            "synthetic": bool(emp.is_synthetic),
            "test": bool(emp.is_test),  # тестовый новичок команды: только его можно перематывать в демо
        }

    async def plan_step(self, emp) -> dict:
        """Шаг «Адаптационный план» (отмашка plan_ready): {"at": когда уйдёт по сценарию, "sent": ушёл ли}.

        «План готов» только запоминает файл: шаг уходит в своё время и только подключившемуся новичку.
        at — None, если шага плана для этого новичка в сценарии нет.
        """
        out: dict = {"at": None, "sent": False}
        ctx_fn = optional("app.scenario.engine", "employee_ctx")
        plan_for = optional("app.scenario.schedule", "plan_for")
        if self.plan is None or ctx_fn is None or plan_for is None:
            return out
        try:
            due = [p for p in plan_for(ctx_fn(emp, _tz(emp).key), self.plan.specs_for(emp))
                   if p.gate == "plan_ready"]
            step_ids = [self.plan.by_number[p.step_number].id for p in due]
        except Exception as e:  # noqa: BLE001 — без сценария карточка всё равно открывается
            log.warning("План адаптации (%s): %s", emp.id, e)
            return out
        if not due:
            return out
        sent = await self.s.scalar(select(func.count()).select_from(Delivery).where(
            Delivery.employee_id == emp.id, Delivery.step_id.in_(step_ids), Delivery.status == "sent"))
        out.update(at=iso(min(p.due_at for p in due)), sent=bool(sent))
        return out

    async def full(self, emp) -> dict:
        tl = await self.timeline(emp, limit=5)
        surveys = await self.surveys(emp) if self.hr else []
        data = await self.short(emp, tl=tl, surveys=surveys)
        intro = intro_of(emp)
        own = self.tickets.get(emp.id, []) if self.hr else []
        data.update({
            "manager": contact(self.staff.get(emp.manager_id), "manager", -1),
            "recruiter": contact(self.staff.get(emp.recruiter_id), "recruiter", -2),
            "intro_text": intro["text"] if intro else None,
            "intro": intro,
            "steps": self.steps(emp, tl),
            "surveys": surveys,
            "tickets": [ticket_short_json(t, emp, self.levels.get(t.id, set()), self.topics.get(t.id))
                        for t in own],
            "meetings": self.meetings(emp),
            "invite_link": invite_link(emp.invite_code),
            "gates": gates_of(emp),
            "plan_step": await self.plan_step(emp),
            "status": emp.status,
            "chats": await self.chats(emp),
        })
        return data

    async def chats(self, emp) -> list[dict]:
        """Чаты команды: куда бот добавил новичка и куда отправил ссылку (app.chats.service.card)."""
        fn = optional("app.chats.service", "card")
        if fn is None:
            return []
        try:
            return list(await call(fn, self.s, emp) or [])
        except Exception as e:  # noqa: BLE001 — без чатов карточка всё равно открывается
            log.warning("Чаты команды (%s): %s", emp.id, e)
            return []


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
