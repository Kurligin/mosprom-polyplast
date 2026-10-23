"""Движок сценария: тик планировщика, отправка шагов, кнопки `sc:`, отмашки, машина времени.

Как шаги уходят новичку:
- Тик раз в минуту берёт активных новичков, прошедших знакомство (`dialog_data.onboarded`), и считает
  наступившие шаги (`schedule.due_steps`): часовой пояс площадки + `time_shift_hours`. Шаги старше суток
  не отправляются, а помечаются `skipped`. Шаги 0 и 1.1–1.4 покрывает знакомство в боте: помечаем `sent`.
- Каждый шаг сначала «занимается» строкой `deliveries` с ключом идемпотентности `<ключ шага>:<id новичка>`
  (INSERT … ON CONFLICT DO NOTHING) и только потом отправляется. Два отправителя одного шага не пошлют.
- Минимум сообщений: подряд идущие текстовые шаги одного момента склеиваются; шаг с кнопками или
  вложением — отдельное сообщение, текстовые шаги перед ним в тот же момент идут его подводкой.
- Ответ на кнопку меняет то же сообщение (`cb.replace`): текст + «✅ выбор» + реплика из ТЗ.
  Вложение по ветке (файл «где пообедать», карта) — новым сообщением.
- Шаг-опрос → `app.surveys.engine.start_survey`. Реплики после опроса в тот же момент («Спасибо за
  обратную связь!», прощание) ждут, пока новичок пройдёт опрос.
- Шаг-письмо (`channel=email`) в бот не уходит.

Машина времени (`time_travel`) для демо:
- переносит «сейчас» новичка на конец целевого дня: сразу после последнего шага этого дня,
  поэтому тик присылает все шаги этого дня, по одному сообщению на момент;
- шаги прошлых дней без отправки помечает `skipped` («перемотка»), поэтому пропущенное не вываливается;
- у тестового новичка (`is_test`) отправки с целевого дня и позже удаляются: можно перемотать назад
  и показать день заново;
- шаги по отмашке не пропускаются: отмашка может прийти позже;
- если бот запущен, отправка начинается сразу, не дожидаясь тика.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.bot import runtime, uploads
from app.bot.keyboards import callback, keyboard
from app.bot.texts import tv
from app.config import settings
from app.db.models import Delivery, Employee, ScenarioStep, Site
from app.scenario import mockups, store
from app.scenario import texts as T
from app.scenario.models import EmployeeCtx, StepSpec
from app.scenario.schedule import (
    STALE_AFTER,
    add_months,
    branch_targets,
    due_steps,
    employee_now,
    plan_for,
)
from app.scenario.workcal import next_workday

log = logging.getLogger(__name__)

GATES = ("recruiter_go", "plan_ready")
INTRO_STATE = "sc:intro"
COVERED_NOTE = "знакомство в боте"
TRAVEL_NOTE = "перемотка времени"
STALE_NOTE = "устарел"
EMAIL_NOTE = "письмо, не в боте"
TRAVEL_TARGETS = ("-2", "1", "3", "14", "60", "end", "now")
TRAVEL_EARLIEST = time(10, 0)  # «сейчас» после перемотки — не раньше 10:00 площадки
MAX_TEXT = 3500

_X_RE = re.compile(r"(?<!\w)Х(?!\w)")
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_background: set[asyncio.Task] = set()
_warned: set[str] = set()


def utcnow() -> datetime:
    return datetime.now(UTC)


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


# --- новичок и время --------------------------------------------------------------

def _loaded_tz(emp) -> str:
    """Часовой пояс площадки без ленивой загрузки: только если площадка уже загружена."""
    site = vars(emp).get("site") if hasattr(emp, "__dict__") else None
    return getattr(site, "tz", None) or settings.tz_default


async def _tz(session, emp) -> str:
    site = vars(emp).get("site") if hasattr(emp, "__dict__") else None
    if site is None and session is not None:
        site = await session.get(Site, emp.site_code)
    return getattr(site, "tz", None) or settings.tz_default


def _probation_end(emp) -> date:
    return emp.probation_end or (add_months(emp.start_date, 3) - timedelta(days=1))


def employee_ctx(emp, tz: str) -> EmployeeCtx:
    wt = emp.work_type if emp.work_type in ("office", "production") else "office"
    return EmployeeCtx(start_date=emp.start_date, probation_end=_probation_end(emp), site_code=emp.site_code,
                       work_type=wt, tz=tz, time_shift_hours=emp.time_shift_hours or 0)


def first_day(emp) -> date:
    """День выхода: первый рабочий день начиная с даты выхода (если HR указал выходной — понедельник)."""
    return next_workday(emp.start_date)


def adaptation_day(emp, now: datetime | None = None) -> int:
    """День адаптации: 1 — день выхода, 0 и меньше — до выхода. С учётом машины времени."""
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    vnow = now.astimezone(ZoneInfo(_loaded_tz(emp))) + timedelta(hours=emp.time_shift_hours or 0)
    return (vnow.date() - first_day(emp)).days + 1


def probation_days(emp) -> int:
    """Длина испытательного срока в днях (день выхода — первый)."""
    return (_probation_end(emp) - first_day(emp)).days + 1


def gates(emp) -> dict:
    """Открытые отмашки: {"recruiter_go": bool, "plan_ready": bool}."""
    raw = (emp.dialog_data or {}).get("gates") or {}
    return {g: bool(raw.get(g)) for g in GATES}


def _gate_file(emp, gate: str) -> str | None:
    raw = ((emp.dialog_data or {}).get("gates") or {}).get(gate)
    return raw.get("file") if isinstance(raw, dict) else None


def _onboarded(emp) -> bool:
    return bool((emp.dialog_data or {}).get("onboarded"))


def _set_data(emp, **kw) -> None:
    emp.dialog_data = {**(emp.dialog_data or {}), **kw}


def personalize(text: str, emp) -> str:
    """«Х» и «Имя кандидата» → имя новичка. Остальной текст шага — дословно."""
    name = emp.first_name or ""
    return _X_RE.sub(name, (text or "").replace("Имя кандидата", name))


# --- сценарий из БД ----------------------------------------------------------------

class Plan:
    """Активные шаги сценария: строки БД и их StepSpec (для расписания)."""

    def __init__(self, rows: list[ScenarioStep]):
        self.rows = rows
        self.by_number = {r.order_no: r for r in rows}
        self.by_key = {r.key: r for r in rows}
        self.specs = [store.spec_from_row(r) for r in rows]

    def specs_for(self, emp) -> list[StepSpec]:
        """Шаги с поправкой: план от HR с файлом уходит и производству, хотя в ТЗ шаг «для офиса»."""
        if not _gate_file(emp, "plan_ready"):
            return self.specs
        return [s.model_copy(update={"office_only": False}) if s.gate == "plan_ready" else s for s in self.specs]


async def load_plan(session) -> Plan:
    return Plan(await store.active_steps(session))


def _ex(row: ScenarioStep) -> dict:
    return row.extra or {}


def _ikey(row: ScenarioStep, emp_id: int, suffix: str = "") -> str:
    return f"{row.key}:{emp_id}{suffix}"


async def _deliveries(session, emp_id: int) -> dict[str, Delivery]:
    rows = (await session.scalars(select(Delivery).where(Delivery.employee_id == emp_id))).all()
    return {d.idempotency_key: d for d in rows}


async def _claim(session, emp, items: list[tuple[ScenarioStep, datetime]], *, status: str = "planned",
                 note: str | None = None, suffix: str = "") -> dict[int, Delivery]:
    """Занять отправку шагов: строка deliveries с ключом идемпотентности. → {step_id: Delivery} занятых нами."""
    if not items:
        return {}
    now = utcnow()
    values = [{"employee_id": emp.id, "step_id": row.id, "scheduled_at": at, "status": status,
               "idempotency_key": _ikey(row, emp.id, suffix), "error": note,
               "sent_at": now if status == "sent" else None} for row, at in items]
    stmt = (insert(Delivery).values(values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Delivery.id))
    ids = list((await session.execute(stmt)).scalars().all())
    if not ids:
        return {}
    got = (await session.scalars(select(Delivery).where(Delivery.id.in_(ids)))).all()
    return {d.step_id: d for d in got}


# --- отправка ------------------------------------------------------------------------

def _on_demand(row: ScenarioStep) -> bool:
    """Файл отдаётся по кнопке («Покажи карту»), а не вместе с сообщением."""
    return any(b.get("action") == "send_attachment" for b in row.buttons or [])


def _attachment_path(row: ScenarioStep, emp) -> Path | None:
    if _ex(row).get("gate") == "plan_ready":
        f = _gate_file(emp, "plan_ready")
        if f and Path(f).exists():
            return Path(f)
    if not row.attachment or _on_demand(row):
        return None
    return mockups.ensure_asset(row.attachment, row.topic)


def _is_rich(row: ScenarioStep, emp) -> bool:
    return bool(row.buttons) or _attachment_path(row, emp) is not None


async def _attach(path: Path | None, repo, sender) -> list[dict]:
    if path is None:
        return []
    try:
        return [await uploads.attachment(path, repo=repo, sender=sender)]
    except Exception as e:  # noqa: BLE001 — без картинки шаг всё равно уходит
        log.warning("Вложение %s не загружено: %s", path.name, e)
        return []


async def _send(sender, repo, emp, text: str, attachments: list[dict]) -> list[str] | None:
    try:
        return await uploads.send(sender, repo, text, employee=emp, attachments=attachments or None)
    except Exception as e:  # noqa: BLE001
        log.warning("Шаг новичку %s не отправлен: %s", emp.id, e)
        return None


def _join(rows: list[ScenarioStep], emp) -> str:
    return "\n\n".join(personalize(r.text, emp).strip() for r in rows if (r.text or "").strip())


def _units(rows: list[ScenarioStep], emp, survey_ids: set[int]) -> list[tuple[str, list[ScenarioStep]]]:
    """Шаги одного момента → сообщения. ("msg", шаги) — одно сообщение, ("survey", шаги) — один опрос."""
    out: list[tuple[str, list[ScenarioStep]]] = []
    lead: list[ScenarioStep] = []

    def flush():
        nonlocal lead
        if lead:
            out.append(("msg", lead))
            lead = []

    for row in rows:
        if row.id in survey_ids:
            flush()
            if out and out[-1][0] == "survey":
                out[-1][1].append(row)
            else:
                out.append(("survey", [row]))
        elif _is_rich(row, emp):
            if len(_join([*lead, row], emp)) > MAX_TEXT:
                flush()
            out.append(("msg", [*lead, row]))
            lead = []
        else:
            if lead and len(_join([*lead, row], emp)) > MAX_TEXT:
                flush()
            lead.append(row)
    flush()
    return out


def _surveys():
    """Модуль опросов (app.surveys.engine) или None, если его ещё нет."""
    try:
        from app.surveys import engine as sv
    except ImportError:
        return None
    return sv if hasattr(sv, "start_survey") and hasattr(sv, "survey_for_step") else None


def _survey_code(sv, row: ScenarioStep) -> str | None:
    """Код опроса для шага: модуль опросов сам решает, какие шаги сценария — его (по номеру шага)."""
    if sv is None:
        return None
    try:
        return sv.survey_for_step(row.order_no) or None
    except Exception:  # noqa: BLE001
        return None


async def _send_survey(session, repo, emp, rows: list[ScenarioStep], at: datetime, sv, commit: bool) -> int:
    claimed = await _claim(session, emp, [(r, at) for r in rows])
    if commit:
        await session.commit()
    started: set[str] = set()
    for row in rows:
        d = claimed.get(row.id)
        if d is None:
            continue
        code = _survey_code(sv, row)
        if code is None:
            d.status, d.error = "skipped", "нет опроса для шага"
            log.info("Шаг-опрос %s: опрос не найден", row.key)
            continue
        if code in started:
            d.status, d.sent_at, d.error = "sent", utcnow(), f"в составе опроса {code}"
            continue
        try:
            mid = await sv.start_survey(repo, emp, code)
        except Exception as e:  # noqa: BLE001
            log.warning("Опрос %s новичку %s не начат: %s", code, emp.id, e)
            d.status, d.error = "failed", str(e)[:500]
            continue
        started.add(code)
        d.status, d.sent_at, d.mid = "sent", utcnow(), mid
        d.error = f"опрос {code}" if mid else f"опрос {code} уже был"
    return len(started)


async def _send_msg(session, repo, emp, rows: list[ScenarioStep], at: datetime, sender, commit: bool) -> int:
    claimed = await _claim(session, emp, [(r, at) for r in rows])
    rows = [r for r in rows if r.id in claimed]
    if not rows:
        return 0
    if commit:
        await session.commit()
    last = rows[-1]
    atts = await _attach(_attachment_path(last, emp), repo, sender)
    if last.buttons:
        did = claimed[last.id].id
        atts += keyboard(*[[callback(b.get("text") or "…", f"sc:{did}:{i}")] for i, b in enumerate(last.buttons)])
    mids = await _send(sender, repo, emp, _join(rows, emp), atts)
    for r in rows:
        d = claimed[r.id]
        if mids:
            d.status, d.sent_at, d.mid = "sent", utcnow(), mids[-1]
        else:
            d.status, d.error = "failed", "не доставлено (бот остановлен или ошибка API)"
    if mids and any(_ex(r).get("gate") == "recruiter_go" for r in rows):
        emp.dialog_state = INTRO_STATE
    return 1 if mids else 0


def _survey_done(emp, sv, survey_row: ScenarioStep | None, survey_delivery: Delivery | None) -> bool:
    """Реплики после опроса можно слать: опрос пройден (или для шага опроса нет)."""
    if survey_delivery is None or survey_row is None:
        return False
    if survey_delivery.status in ("skipped", "failed"):
        return True
    code = _survey_code(sv, survey_row)
    if code is None:
        return False
    try:
        hist = (sv.survey_history(emp) or {}).get(code) or {}
    except Exception:  # noqa: BLE001
        return False
    return bool(hist.get("done_at"))


async def deliver_due(session, emp, *, sender, repo=None, now: datetime | None = None,
                      plan: Plan | None = None, commit: bool = False) -> int:
    """Отправить новичку все наступившие шаги. → число отправленных сообщений и начатых опросов.

    commit=True — коммитить после «занятия» шага и после отправки (тик). В обработчике бота — False:
    коммитит runner после события.
    """
    from app.bot.repo import SqlRepo

    now = now or utcnow()
    repo = repo or SqlRepo(session)
    plan = plan or await load_plan(session)
    if not plan.rows:
        return 0
    sv = _surveys()
    async with _locks[emp.id]:
        tz = await _tz(session, emp)
        ctx = employee_ctx(emp, tz)
        vnow = employee_now(ctx, now)
        specs = plan.specs_for(emp)
        delivered = await _deliveries(session, emp.id)

        def has(row: ScenarioStep) -> bool:
            return _ikey(row, emp.id) in delivered

        planned = {p.step_number: p.due_at for p in plan_for(ctx, specs)}

        # 0, 1.1–1.4 — знакомство в боте
        covered = [(r, planned[r.order_no]) for r in plan.rows
                   if _ex(r).get("covered") and r.order_no in planned and not has(r)]
        await _claim(session, emp, covered, status="sent", note=COVERED_NOTE)

        # устаревшие — пометить, чтобы не висели «впереди»
        start_dt = datetime.combine(ctx.start_date, time(0, 0), tzinfo=ZoneInfo(tz))
        stale = []
        for s in specs:
            due = planned.get(s.number)
            row = plan.by_number[s.number]
            if due is None or has(row) or s.gate or _ex(row).get("covered"):
                continue
            pre_start = s.timing is not None and s.timing.kind == "before_start" and vnow < start_dt
            if vnow - due > STALE_AFTER and not pre_start:
                stale.append((row, due))
        await _claim(session, emp, stale, status="skipped", note=STALE_NOTE)

        sent_numbers = {r.order_no for r in plan.rows if has(r) or _ex(r).get("covered")}
        sent_numbers |= {r.order_no for r, _ in stale}
        due = due_steps(ctx, specs, now, sent_numbers, {g for g, v in gates(emp).items() if v})

        email, ready = [], []
        for p in due:
            row = plan.by_number[p.step_number]
            if _ex(row).get("channel") == "email":
                email.append((row, p.due_at))
                continue
            sk = _ex(row).get("after_survey")
            if sk and _survey_code(sv, row) is None and \
                    not _survey_done(emp, sv, plan.by_key.get(sk), delivered.get(f"{sk}:{emp.id}")):
                continue  # ждём, пока новичок пройдёт опрос
            if (row.kind == "survey" or _survey_code(sv, row)) and sv is None:
                _warn_once("surveys", "Модуль опросов не подключён: шаги-опросы ждут")
                continue
            ready.append((row, p.due_at))
        await _claim(session, emp, email, status="skipped", note=EMAIL_NOTE)
        await session.flush()
        if commit:
            await session.commit()

        survey_ids = {r.id for r, _ in ready if r.kind == "survey" or _survey_code(sv, r)}
        sent = 0
        moments: dict[datetime, list[ScenarioStep]] = defaultdict(list)
        for row, at in ready:
            moments[at].append(row)
        for at in sorted(moments):
            rows = sorted(moments[at], key=lambda r: r.order_no)
            for kind, unit in _units(rows, emp, survey_ids):
                if kind == "survey":
                    sent += await _send_survey(session, repo, emp, unit, at, sv, commit)
                else:
                    sent += await _send_msg(session, repo, emp, unit, at, sender, commit)
                await session.flush()
                if commit:
                    await session.commit()
        return sent


def _active_query():
    return (select(Employee)
            .where(Employee.status == "active", Employee.dialog_data["onboarded"].as_boolean().is_(True))
            .where((Employee.chat_id.is_not(None)) | (Employee.max_user_id.is_not(None)))
            .order_by(Employee.id))


async def tick(now: datetime | None = None, *, sender=None, session_factory=None) -> int:
    """Задача scenario_tick: наступившие шаги всем активным новичкам. → число сообщений."""
    if sender is None:
        ctx = runtime.get_ctx()
        if ctx is None:
            return 0  # бот выключен: шаги дождутся его запуска
        sender = ctx.sender
    if session_factory is None:
        from app.db.session import async_session as session_factory
    total = 0
    async with session_factory() as session:
        plan = await load_plan(session)
        if not plan.rows:
            return 0
        ids = list((await session.scalars(_active_query().with_only_columns(Employee.id))).all())
    for emp_id in ids:
        async with session_factory() as session:
            try:
                emp = await session.get(Employee, emp_id)
                if emp is None:
                    continue
                plan = await load_plan(session)
                total += await deliver_due(session, emp, sender=sender, now=now, plan=plan, commit=True)
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception("scenario_tick: новичок %s", emp_id)
    if total:
        log.info("scenario_tick: отправлено %s", total)
    return total


def _deliver_soon(emp_id: int, delay: float = 1.5) -> None:
    """Отправить наступившие шаги в фоне, когда вызывающий код закоммитит изменения (API)."""
    ctx = runtime.get_ctx()
    if ctx is None:
        return

    async def run():
        await asyncio.sleep(delay)
        from app.db.session import async_session

        async with async_session() as session:
            try:
                emp = await session.get(Employee, emp_id)
                if emp is not None and _onboarded(emp) and emp.status == "active":
                    await deliver_due(session, emp, sender=ctx.sender, commit=True)
                    await session.commit()
            except Exception:
                await session.rollback()
                log.exception("Отправка шагов новичку %s", emp_id)

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _background.add(task)
    task.add_done_callback(_background.discard)


# --- знакомство, кнопки, команды ------------------------------------------------------

async def on_onboarded(ctx, repo, emp) -> None:
    """Новичок прошёл знакомство: сразу наступившие шаги (например, «за 2–3 дня до выхода»)."""
    await ctx.sender.typing(emp.chat_id)  # ошибки typing глушит сам Sender
    await deliver_due(repo.s, emp, sender=ctx.sender, repo=repo)


async def _message_rows(session, d: Delivery) -> list[ScenarioStep]:
    """Шаги, склеенные в сообщение с отправкой d (одинаковый mid)."""
    if not d.mid:
        step = await session.get(ScenarioStep, d.step_id)
        return [step] if step else []
    q = (select(ScenarioStep).join(Delivery, Delivery.step_id == ScenarioStep.id)
         .where(Delivery.employee_id == d.employee_id, Delivery.mid == d.mid)
         .order_by(ScenarioStep.order_no))
    return list((await session.scalars(q)).all())


async def on_button(cb, person) -> None:
    """Кнопка шага `sc:<delivery_id>:<n>` (или `sc:<delivery_id>:ra` — напомнить про доступы)."""
    parts = cb.payload.split(":")
    if cb.is_staff or len(parts) != 3 or not parts[1].isdigit():
        await cb.mark()
        return
    emp, repo = person, cb.repo
    session = repo.s
    d = await session.get(Delivery, int(parts[1]))
    step = await session.get(ScenarioStep, d.step_id) if d and d.step_id else None
    if d is None or step is None or d.employee_id != emp.id:
        await cb.mark()
        return
    rows = await _message_rows(session, d) or [step]
    base = _join(rows, emp)
    media = await _attach(_attachment_path(step, emp), repo, cb.ctx.sender)

    if parts[2] == "ra":
        ok = await _remind_access(repo, emp)
        await cb.replace(f"{cb.orig_text or base}\n\n{tv(emp, T.ACCESS_DONE if ok else T.ACCESS_FAIL)}",
                         media, employee=emp)
        return
    if not parts[2].isdigit() or int(parts[2]) >= len(step.buttons or []):
        await cb.mark()
        return
    btn = step.buttons[int(parts[2])]
    label = btn.get("text") or ""
    text = f"{base}\n\n✅ {label}"
    if btn.get("reply"):
        text += "\n\n" + personalize(btn["reply"], emp)
    kb = keyboard([callback(T.ACCESS_BUTTON, f"sc:{d.id}:ra")]) if btn.get("followup") == "remind_access" else []
    await cb.replace(text, media + kb, employee=emp)

    answers = dict((emp.dialog_data or {}).get("sc_answers") or {})
    answers[step.key] = label
    _set_data(emp, sc_answers=answers)

    if btn.get("action") == "branch":
        await _send_branch(cb, emp, step, label, d)
    elif btn.get("action") == "send_attachment" and step.attachment:
        path = mockups.ensure_asset(step.attachment, step.topic)
        atts = await _attach(path, repo, cb.ctx.sender)
        await _send(cb.ctx.sender, repo, emp, tv(emp, T.MAP_CAPTION), atts)


async def _send_branch(cb, emp, step: ScenarioStep, label: str, src: Delivery) -> None:
    """«Да!» → шаг-ответ своей площадки (файл «где пообедать») новым сообщением."""
    session, repo = cb.repo.s, cb.repo
    plan = await load_plan(session)
    ctx = employee_ctx(emp, await _tz(session, emp))
    targets = [plan.by_number[s.number] for s in branch_targets(plan.specs, step.order_no, label, ctx)]
    vnow = employee_now(ctx, utcnow())
    for row in targets:
        claimed = await _claim(session, emp, [(row, vnow)], suffix=f":b{src.id}")
        d = claimed.get(row.id)
        if d is None:
            continue
        atts = await _attach(_attachment_path(row, emp), repo, cb.ctx.sender)
        mids = await _send(cb.ctx.sender, repo, emp, personalize(row.text, emp), atts)
        d.status, d.sent_at, d.mid = ("sent", utcnow(), mids[-1]) if mids else ("failed", None, None)


async def _remind_access(repo, emp) -> bool:
    try:
        from app.manager.service import remind_access
    except ImportError:
        _warn_once("manager", "Модуль руководителя не подключён: напоминание про доступы не отправлено")
        return False
    try:
        return bool(await remind_access(repo, emp))
    except Exception as e:  # noqa: BLE001
        log.warning("remind_access(%s): %s", emp.id, e)
        return False


async def _food_row(session, emp) -> ScenarioStep | None:
    plan = await load_plan(session)
    for row in plan.rows:
        if (row.attachment or "").startswith("food_") and \
                (not row.site_tags or emp.site_code in row.site_tags):
            return row
    return None


async def send_food(ctx, repo, emp, arg: str = "") -> None:
    """/food — тот же файл «где пообедать», что и по кнопке «Да!»."""
    row = await _food_row(repo.s, emp)
    name = row.attachment if row is not None else f"food_{emp.site_code}.pdf"
    path = mockups.ensure_asset(name, "Где пообедать")
    atts = await _attach(path, repo, ctx.sender)
    if not atts:
        await _send(ctx.sender, repo, emp, tv(emp, T.FOOD_NONE), [])
        return
    tail = personalize(row.text, emp) if row is not None else ""
    await _send(ctx.sender, repo, emp, "\n\n".join(x for x in (tv(emp, T.FOOD_INTRO), tail) if x), atts)


# --- представление коллегам (отмашка recruiter_go) --------------------------------------

def _photo_url(msg: dict) -> str | None:
    for att in (msg or {}).get("attachments") or []:
        if att.get("type") == "image":
            payload = att.get("payload") or {}
            return payload.get("url") or payload.get("token")
    return None


async def on_intro(ctx, repo, emp, text: str, msg: dict) -> None:
    """Состояние sc:intro: новичок присылает рассказ о себе и фото → dialog_data["intro"]."""
    photo = _photo_url(msg)
    text = (text or "").strip()
    if text and not photo and text.endswith("?") and len(text) < 200:
        # похоже на вопрос, а не на рассказ о себе: отвечаем как на вопрос и ждём дальше
        from app.bot.handlers import route_free_text

        await route_free_text(ctx, repo, emp, text)
        return
    intro = dict((emp.dialog_data or {}).get("intro") or {})
    if text:
        intro["text"] = f"{intro['text']}\n{text}" if intro.get("text") else text
    if photo:
        intro["photo_url"] = photo
    intro["at"] = utcnow().isoformat()
    _set_data(emp, intro=intro)
    if intro.get("text") and intro.get("photo_url"):
        emp.dialog_state = None
        await _send(ctx.sender, repo, emp, tv(emp, T.INTRO_THANKS), [])
        await runtime.notify_hr_group(repo, T.HR_GROUP_INTRO.format(name=emp.full_name, site=emp.site_code))
    elif intro.get("text"):
        await _send(ctx.sender, repo, emp, tv(emp, T.INTRO_NEED_PHOTO), [])
    else:
        await _send(ctx.sender, repo, emp, tv(emp, T.INTRO_NEED_TEXT), [])


# --- отмашки, ближайшие шаги, машина времени ---------------------------------------------

async def open_gate(session, emp, gate: str, *, file_path: str | None = None) -> dict:
    """Отмашка рекрутера/HR: recruiter_go — «представлю тебя коллегам», plan_ready — план с файлом.

    Шаг уходит в своё время по сценарию; если время уже прошло — сразу (или на ближайшем тике).
    """
    if gate not in GATES:
        raise ValueError(f"Неизвестная отмашка: {gate}")
    raw = dict((emp.dialog_data or {}).get("gates") or {})
    raw[gate] = {"at": utcnow().isoformat(), "file": file_path}
    _set_data(emp, gates=raw)
    await session.flush()
    if _onboarded(emp) and emp.status == "active":
        _deliver_soon(emp.id)
    return gates(emp)


def step_title(row: ScenarioStep, emp=None) -> str:
    """Короткий заголовок шага для Mini App (store.TITLES), с именем вместо «Х»."""
    title = _ex(row).get("title")
    if not title:
        spec = store.spec_from_row(row)
        title = store.title_for(spec) or (spec.topic or "Сообщение Корсика")
    return personalize(title, emp) if emp is not None else title


async def upcoming_steps(session, emp, limit: int = 5, *, now: datetime | None = None) -> list[dict]:
    """Ближайшие шаги новичка: [{"at": iso, "title": str}], один пункт на момент (до двух заголовков)."""
    plan = await load_plan(session)
    ctx = employee_ctx(emp, await _tz(session, emp))
    vnow = employee_now(ctx, now or utcnow())
    delivered = await _deliveries(session, emp.id)
    moments: dict[datetime, list[str]] = {}
    for p in plan_for(ctx, plan.specs_for(emp)):
        row = plan.by_number[p.step_number]
        if p.due_at <= vnow or _ikey(row, emp.id) in delivered or _ex(row).get("covered") \
                or _ex(row).get("channel") == "email":
            continue
        if p.due_at not in moments:
            if len(moments) >= limit:
                break
            moments[p.due_at] = []
        title = step_title(row, emp)
        if title and title not in moments[p.due_at]:
            moments[p.due_at].append(title)
    return [{"at": at.isoformat(), "title": " · ".join(t[:2]) or "Сообщение Корсика"}
            for at, t in moments.items()]


def _target_date(to: str, emp, ctx: EmployeeCtx, plan: Plan) -> date:
    """Дата, на которую перематываем: ключевой день, подтянутый к ближайшему дню с шагами."""
    specs = {s.number: s for s in plan.specs_for(emp)}
    plan_ = plan_for(ctx, list(specs.values()))
    planned = sorted({p.due_at.date() for p in plan_ if not p.gate})
    if to == "-2":
        pre = [p.due_at.date() for p in plan_
               if specs[p.step_number].timing and specs[p.step_number].timing.kind == "before_start"]
        return min(pre) if pre else first_day(emp) - timedelta(days=3)
    if to == "end":
        target = next_workday(ctx.probation_end)
    else:
        target = first_day(emp) + timedelta(days=int(to) - 1)
    near = [d for d in planned if target <= d <= target + timedelta(days=10)]
    return near[0] if near else target


async def _manager_tick(session) -> None:
    """После перемотки — сразу задачи руководителя (push «проведи встречу»), не ждать 5 минут."""
    try:
        from app.manager.service import tick as manager_tick
    except ImportError:
        return
    try:
        await manager_tick(session)
    except Exception as e:  # noqa: BLE001 — перемотка не должна падать из-за push руководителю
        log.warning("manager.tick после перемотки: %s", e)


def _forget_gates(emp, ctx: EmployeeCtx, plan: Plan, since: datetime) -> None:
    """Тестовый новичок перематывается назад: отмашки для шагов этих дней закрываем (и рассказ о себе)."""
    raw = dict((emp.dialog_data or {}).get("gates") or {})
    if not raw:
        return
    for p in plan_for(ctx, plan.specs_for(emp)):
        if p.gate and p.gate in raw and p.due_at >= since:
            raw.pop(p.gate)
            if p.gate == "recruiter_go":
                _set_data(emp, intro=None)
                if emp.dialog_state == INTRO_STATE:
                    emp.dialog_state = None
    _set_data(emp, gates=raw)


async def _forget_surveys(session, emp, since: datetime) -> None:
    """Тестовый новичок перематывается назад: опросы этих дней можно пройти заново."""
    sv = _surveys()
    if sv is None or not hasattr(sv, "reset"):
        return
    rows = (await session.scalars(
        select(ScenarioStep).join(Delivery, Delivery.step_id == ScenarioStep.id)
        .where(Delivery.employee_id == emp.id, Delivery.scheduled_at >= since))).all()
    for code in {c for c in (_survey_code(sv, r) for r in rows) if c}:
        try:
            sv.reset(emp, code)
        except Exception as e:  # noqa: BLE001
            log.warning("surveys.reset(%s): %s", code, e)


async def time_travel(session, emp, to: str, *, now: datetime | None = None) -> dict:
    """Машина времени для демо. to ∈ -2 | 1 | 3 | 14 | 60 | end (и now — вернуть настоящее время).

    → {"employee_id", "day", "message"}. Коммит делает вызывающий код.
    """
    to = str(to).strip()
    if to not in TRAVEL_TARGETS:
        raise ValueError(f"Неизвестная точка перемотки: {to}")
    name = (emp.first_name or emp.full_name or "").strip()
    now = now or utcnow()
    if to == "now":
        emp.time_shift_hours = 0
        await session.flush()
        return {"employee_id": emp.id, "day": adaptation_day(emp, now),
                "message": T.TRAVEL_NOW.format(name=name, day=adaptation_day(emp, now))}

    plan = await load_plan(session)
    tz_name = await _tz(session, emp)
    tz = ZoneInfo(tz_name)
    ctx = employee_ctx(emp, tz_name).model_copy(update={"time_shift_hours": 0})
    target = _target_date(to, emp, ctx, plan)
    times = [p.due_at for p in plan_for(ctx, plan.specs_for(emp)) if p.due_at.date() == target and not p.gate]
    landing = (max(times) + timedelta(minutes=1)) if times else datetime.combine(target, time(12, 0), tzinfo=tz)
    # не раньше 10:00 площадки: утренние шаги и push руководителю попадают в демо
    landing = max(landing, datetime.combine(target, TRAVEL_EARLIEST, tzinfo=tz))
    shift = math.ceil((landing - now).total_seconds() / 3600)
    emp.time_shift_hours = shift
    day_start = datetime.combine(target, time(0, 0), tzinfo=tz)

    if emp.is_test:
        # повтор дня: отправки с целевого дня и позже удаляем, их можно показать заново (и опросы, и отмашки)
        await _forget_surveys(session, emp, day_start)
        _forget_gates(emp, ctx, plan, day_start)
        await session.execute(delete(Delivery).where(Delivery.employee_id == emp.id,
                                                     Delivery.scheduled_at >= day_start))
    delivered = await _deliveries(session, emp.id)
    skip = []
    for p in plan_for(ctx, plan.specs_for(emp)):
        row = plan.by_number[p.step_number]
        if p.due_at < day_start and not p.gate and not _ex(row).get("covered") \
                and _ikey(row, emp.id) not in delivered:
            skip.append((row, p.due_at))
    await _claim(session, emp, skip, status="skipped", note=TRAVEL_NOTE)
    await session.flush()

    day = adaptation_day(emp, now)
    if _onboarded(emp) and emp.status == "active":
        _deliver_soon(emp.id)
    await _manager_tick(session)
    log.info("Машина времени: новичок %s → %s (день %s, сдвиг %s ч)", emp.id, to, day, shift)
    return {"employee_id": emp.id, "day": day,
            "message": T.TRAVEL_DONE.format(name=name, day=day, label=T.TRAVEL_LABELS[to])}
