"""Встречи руководителя с новичком и push «проведи встречу».

Встречи (`ManagerTask`):
- 3-й рабочий день;
- 30-й и 60-й день, если выпали на выходной — ближайший рабочий после;
- итоговая — за 5 дней до конца испытательного, если выпала на выходной — ближайший рабочий раньше.
Встречи на 30-й и 60-й день после итоговой не ставим (короткий испытательный).

Push — в день встречи в 10:00 по часовому поясу площадки, «сейчас» новичка = now + time_shift_hours.
Если 10:00 пропустили (перезапуск), push догоняет в ближайшие 3 дня, итоговая — до конца испытательного.
Синтетическим новичкам push не шлём: встречи для Mini App создаём, руководителя не дёргаем.
Ответы опросов и обращения руководитель не получает: модуль их не читает.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot import runtime
from app.bot.keyboards import callback, keyboard, open_app
from app.config import settings
from app.db.models import Employee, ManagerTask, Staff
from app.manager import texts as T
from app.scenario.workcal import is_workday, next_workday, nth_workday

log = logging.getLogger(__name__)

KIND_MEETING = "meeting"
KIND_PROBATION = "probation"
MEETING_DAYS = (3, 30, 60)
PROBATION_LEAD_DAYS = 5
PUSH_AT = time(10, 0)
PUSH_GRACE_DAYS = 3
ACCESS_COOLDOWN = timedelta(minutes=30)
DEFAULT_PROBATION_DAYS = 90


@dataclass(frozen=True)
class Milestone:
    kind: str
    day: int     # 3 / 30 / 60; у итоговой — день адаптации даты встречи
    due: date


# --- время новичка -----------------------------------------------------------

def _tz(emp) -> str:
    # без ленивой загрузки: площадку берём, только если она уже загружена
    site = vars(emp).get("site")
    return getattr(site, "tz", None) or settings.tz_default


def employee_now(emp, now: datetime | None = None) -> datetime:
    """«Сейчас» новичка: часовой пояс площадки + машина времени."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(ZoneInfo(_tz(emp))) + timedelta(hours=emp.time_shift_hours or 0)


def _engine_fn(name: str):
    """Функция из app.scenario.engine, если модуль уже есть."""
    try:
        from app.scenario import engine
    except ImportError:
        return None
    return getattr(engine, name, None)


def adaptation_day(emp, now: datetime | None = None) -> int:
    """1 — день выхода, ≤ 0 — до выхода. Берём из движка сценария, пока его нет — считаем сами."""
    fn = _engine_fn("adaptation_day")
    if fn is not None:
        try:
            return int(fn(emp, now))
        except Exception as e:  # noqa: BLE001 — движок не должен ломать экран руководителя
            log.debug("engine.adaptation_day: %s", e)
    return (employee_now(emp, now).date() - emp.start_date).days + 1


def probation_days(emp) -> int:
    """Длина испытательного срока в днях."""
    fn = _engine_fn("probation_days")
    if fn is not None:
        try:
            return int(fn(emp))
        except Exception as e:  # noqa: BLE001
            log.debug("engine.probation_days: %s", e)
    if emp.probation_end:
        return max((emp.probation_end - emp.start_date).days, 1)
    return DEFAULT_PROBATION_DAYS


def display_name(emp) -> str:
    """«Фамилия Имя Отчество» → «Имя Фамилия»; пометки в скобках убираем."""
    raw = re.sub(r"\s*[\[(][^\])]*[\])]", "", emp.full_name or "").strip()
    parts = raw.split()
    first = (emp.first_name or "").strip()
    if len(parts) >= 2 and first and parts[0] != first and first in parts[1:]:
        return f"{first} {parts[0]}"
    return raw or first or "Новичок"


# --- план встреч (чистые функции) ------------------------------------------

def prev_workday(d: date) -> date:
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


def plan_meetings(start: date, probation_end: date | None) -> list[Milestone]:
    plan = [
        Milestone(KIND_MEETING, 3, nth_workday(start, 3)),
        Milestone(KIND_MEETING, 30, next_workday(start + timedelta(days=29))),
        Milestone(KIND_MEETING, 60, next_workday(start + timedelta(days=59))),
    ]
    if probation_end:
        due = prev_workday(probation_end - timedelta(days=PROBATION_LEAD_DAYS))
        if due > plan[0].due:
            plan = [m for m in plan if m.due < due]
            plan.append(Milestone(KIND_PROBATION, (due - start).days + 1, due))
    return plan


def push_window(task, emp) -> tuple[date, date]:
    """Дни, когда push по встрече уместен: с даты встречи и ещё немного, если пропустили."""
    due = task.due_date
    if task.kind == KIND_PROBATION:
        return due, max(emp.probation_end or due, due)
    lo = due
    if emp.time_shift_hours and task.day in MEETING_DAYS:
        # демо-перемотка ставит календарный день, он может выпасть на выходной до даты встречи
        lo = min(due, emp.start_date + timedelta(days=task.day - 1))
    return lo, due + timedelta(days=PUSH_GRACE_DAYS)


def push_is_due(task, emp, vnow: datetime) -> bool:
    lo, hi = push_window(task, emp)
    today = vnow.date()
    if not lo <= today <= hi:
        return False
    if emp.time_shift_hours:
        return today > lo or vnow.time() >= PUSH_AT
    return is_workday(today) and vnow.time() >= PUSH_AT


# --- вид сообщений -----------------------------------------------------------

def _app_button() -> dict | None:
    if settings.miniapp_url and settings.max_bot_name:
        return open_app(T.BTN_APP, settings.max_bot_name.lstrip("@"))
    return None


def meeting_title(task) -> str:
    return T.TITLES.get(task.day, T.PROBATION_TITLE) if task.kind == KIND_MEETING else T.PROBATION_TITLE


def push_view(task, emp, vnow: datetime | None = None) -> tuple[str, list[dict]]:
    name = display_name(emp)
    if task.kind == KIND_PROBATION:
        today = (vnow or employee_now(emp)).date()
        text = T.PUSH_PROBATION.format(name=name, when=T.probation_when(emp.probation_end or task.due_date, today))
    else:
        text = T.PUSH.get(task.day, T.PUSH[3]).format(name=name)
    row = [callback(T.BTN_DONE, f"mg:done:{task.id}")]
    app = _app_button()
    if app:
        row.append(app)
    return text, keyboard(row)


def done_view(task, emp) -> tuple[str, list[dict]]:
    label = T.DONE_LABELS.get(task.day, T.PROBATION_DONE_LABEL) if task.kind == KIND_MEETING \
        else T.PROBATION_DONE_LABEL
    app = _app_button()
    return T.DONE.format(name=display_name(emp), label=label), (keyboard([app]) if app else [])


def meeting_dict(task) -> dict:
    """Форма Meeting из miniapp/src/api/types.ts."""
    return {"id": task.id, "day": task.day, "title": meeting_title(task),
            "date": task.due_date.isoformat() if task.due_date else None, "done": task.status == "done"}


# --- БД ----------------------------------------------------------------------

def _key(kind: str, day: int) -> tuple[str, int]:
    return (kind, day if kind == KIND_MEETING else 0)


async def _tasks_by_employee(session, employee_ids: list[int]) -> dict[int, list[ManagerTask]]:
    out: dict[int, list[ManagerTask]] = {i: [] for i in employee_ids}
    if not employee_ids:
        return out
    rows = await session.scalars(select(ManagerTask).where(ManagerTask.employee_id.in_(employee_ids)))
    for t in rows:
        out.setdefault(t.employee_id, []).append(t)
    return out


async def _sync_tasks(session, emp, existing: list[ManagerTask]) -> tuple[list[ManagerTask], int]:
    """Привести встречи новичка к плану. Проведённые и отправленные не трогаем."""
    plan = plan_meetings(emp.start_date, emp.probation_end) if emp.manager_id else []
    wanted = {_key(m.kind, m.day): m for m in plan}
    keep: dict[tuple[str, int], ManagerTask] = {}
    for t in existing:
        key = _key(t.kind, t.day)
        untouched = t.status == "pending" and not t.mid
        if t.manager_id != emp.manager_id or key not in wanted:
            # сменился руководитель или встреча выпала из плана
            if untouched:
                await session.delete(t)
            continue
        keep[key] = t
    created = 0
    for key, m in wanted.items():
        t = keep.get(key)
        if t is None:
            t = ManagerTask(manager_id=emp.manager_id, employee_id=emp.id, day=m.day, due_date=m.due,
                            kind=m.kind, status="pending")
            session.add(t)
            keep[key] = t
            created += 1
        elif t.status == "pending" and (t.due_date != m.due or t.day != m.day):
            # сдвинули дату выхода или конец испытательного
            t.due_date, t.day = m.due, m.day
    await session.flush()
    return sorted(keep.values(), key=lambda t: (t.due_date or date.max, t.id)), created


async def ensure_tasks(session, emp, existing: list[ManagerTask] | None = None) -> list[ManagerTask]:
    """Встречи новичка по плану (создаёт недостающие, flush). Коммит — за вызывающим."""
    if existing is None:
        existing = (await _tasks_by_employee(session, [emp.id]))[emp.id]
    tasks, _ = await _sync_tasks(session, emp, existing)
    return tasks


def _rewind(tasks: list[ManagerTask], emp, vnow: datetime) -> None:
    """Демо: тестового новичка перемотали назад — будущие встречи снова ждут push."""
    today = vnow.date()
    for t in tasks:
        lo, _ = push_window(t, emp)
        if today < lo and (t.mid or t.status != "pending"):
            t.status, t.mid, t.done_at = "pending", None, None


def _bound(staff) -> bool:
    return bool(staff is not None and (staff.chat_id or staff.max_user_id))


async def send_push(session, task, emp, manager, vnow: datetime | None = None) -> bool:
    """Push руководителю. True — отправлен (mid сохранён в задаче)."""
    if not _bound(manager):
        return False
    from app.bot.repo import SqlRepo

    text, atts = push_view(task, emp, vnow)
    mids = await runtime.send(SqlRepo(session), text, staff=manager, attachments=atts)
    if not mids:
        return False
    task.mid = mids[-1]
    log.info("Push руководителю %s: встреча %s (новичок %s)", manager.id, task.id, emp.id)
    return True


async def tick(session, now: datetime | None = None) -> dict:
    """Задача manager_tasks: досоздать встречи и разослать push, чей час настал.

    Можно вызвать сразу после перемотки времени, чтобы не ждать следующего запуска.
    """
    emps = (await session.scalars(
        select(Employee)
        .where(Employee.manager_id.is_not(None), Employee.status != "finished")
        .options(selectinload(Employee.site), selectinload(Employee.manager))
    )).all()
    by_emp = await _tasks_by_employee(session, [e.id for e in emps])
    stats = {"employees": len(emps), "created": 0, "sent": 0}
    for emp in emps:
        tasks, created = await _sync_tasks(session, emp, by_emp.get(emp.id, []))
        stats["created"] += created
        vnow = employee_now(emp, now)
        if emp.is_test:
            _rewind(tasks, emp, vnow)
        if emp.is_synthetic or not _bound(emp.manager):
            continue
        for t in tasks:
            if t.status != "pending" or t.mid or t.manager_id != emp.manager_id or not push_is_due(t, emp, vnow):
                continue
            try:
                if await send_push(session, t, emp, emp.manager, vnow):
                    stats["sent"] += 1
            except Exception:  # один сбой не должен останавливать остальные push
                log.exception("Push по встрече %s не отправлен", t.id)
    await session.flush()
    return stats


async def mark_done(session, task: ManagerTask, staff, *, edit_push: bool = True) -> ManagerTask:
    """Отметить встречу проведённой. Push в чате руководителя меняется на месте («Встреча проведена ✓»).

    PermissionError — руководитель отмечает чужую встречу. HR и глава подбора могут отметить любую.
    """
    if staff is not None and getattr(staff, "role", None) == "manager" and staff.id != task.manager_id:
        raise PermissionError(T.NOT_YOURS)
    if task.status != "done":
        task.status = "done"
        task.done_at = datetime.now(UTC)
    if edit_push and task.mid:
        emp = await session.get(Employee, task.employee_id)
        text, atts = done_view(task, emp)
        await runtime.edit(task.mid, text, atts)
    await session.flush()
    return task


async def remind_access(repo, emp) -> bool:
    """Кнопка новичка «Напомнить руководителю про доступы» → push руководителю.

    Руководитель не подключён к боту — просьба уходит в HR-группу, чтобы не потерялась.
    Повторное нажатие в течение 30 минут не дублирует push. True — кто-то получил просьбу.
    """
    now = datetime.now(UTC)
    data = dict(emp.dialog_data or {})
    last = data.get("access_reminded_at")
    if last:
        try:
            if now - datetime.fromisoformat(last) < ACCESS_COOLDOWN:
                return True
        except ValueError:
            pass
    name = display_name(emp)
    manager = await repo.s.get(Staff, emp.manager_id) if emp.manager_id else None
    sent = None
    if _bound(manager):
        app = _app_button()
        sent = await runtime.send(repo, T.ACCESS.format(name=name), staff=manager,
                                  attachments=keyboard([app]) if app else None)
    if not sent:
        sent = await runtime.notify_hr_group(repo, T.ACCESS_NO_MANAGER.format(name=name))
    if sent:
        data["access_reminded_at"] = now.isoformat()
        emp.dialog_data = data
    return bool(sent)


async def _manager_employees(session, staff) -> list[Employee]:
    return list((await session.scalars(
        select(Employee)
        .where(Employee.manager_id == staff.id, Employee.status != "finished")
        .options(selectinload(Employee.site))
        .order_by(Employee.start_date.desc(), Employee.id)
    )).all())


async def meetings_for(session, emp) -> list[dict]:
    """Встречи новичка для карточки в Mini App (`Employee.meetings`)."""
    return [meeting_dict(t) for t in await ensure_tasks(session, emp)]


async def list_for_manager(session, staff) -> list[dict]:
    """Экран «Мои новички»: день N из M и ближайшая встреча.

    Дописывает недостающие встречи (flush): вызывающий коммитит сессию, чтобы id встреч сохранились.
    """
    emps = await _manager_employees(session, staff)
    by_emp = await _tasks_by_employee(session, [e.id for e in emps])
    out = []
    for emp in emps:
        tasks, _ = await _sync_tasks(session, emp, by_emp.get(emp.id, []))
        meetings = [meeting_dict(t) for t in tasks if t.manager_id == staff.id]
        out.append({
            "id": emp.id,
            "full_name": emp.full_name,
            "site": emp.site_code,
            "work_type": emp.work_type,
            "start_date": emp.start_date.isoformat(),
            "day": adaptation_day(emp),
            "probation_days": probation_days(emp),
            "connected": bool(emp.max_user_id),
            "synthetic": bool(emp.is_synthetic),
            "next_meeting": next((m for m in meetings if not m["done"]), None),
            "meetings": meetings,
        })
    return out
