"""Обращения к HR.

- create_ticket: обращение уходит закреплённому рекрутеру, в HR-группу — уведомление с кнопкой «Открыть в Mini App».
  Если HR-группа не подключена, уведомление получает сам рекрутер в личном чате.
- reply: ответ HR из Mini App уходит новичку с подписью «Имя (HR)». 30 минут после ответа
  сообщения новичка попадают в то же обращение (окно — в emp.dialog_data["ticket_window"]).
- take / close: статус меняется, уведомление в HR-группе правится на месте.

Статусы в БД: new → in_progress → closed. В Mini App new называется open.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.bot import runtime
from app.bot.keyboards import callback, keyboard, open_app
from app.bot.texts import tv
from app.config import settings
from app.db.models import Employee, Flag, HrAnswer, Message, Staff, Ticket
from app.tickets import texts as T

log = logging.getLogger(__name__)

REPLY_WINDOW = timedelta(minutes=30)
WINDOW_KEY = "ticket_window"
QUOTE_LIMIT = 900
DEFAULT_PROBATION_DAYS = 90

RED_CATEGORIES = frozenset({"safety", "harassment", "intent_to_leave", "health"})
# Категория по умолчанию — по источнику обращения
SOURCE_CATEGORY = {
    "button": "contact_request",
    "survey": "contact_request",
    "hr_command": "question",
    "personal": "question",
    "unknown": "unknown",
}
# Тема флага или категория своими словами → код категории
_CATEGORY_HINTS = (
    ("safety", ("safety", "охран", "безопас", "травм", "авари")),
    ("harassment", ("harass", "bully", "травл", "униж", "угроз", "оскорб", "давлен", "буллинг")),
    ("intent_to_leave", ("leave", "quit", "уйти", "уход", "увол")),
    ("health", ("health", "здоров", "болез", "болит", "медиц", "самочув")),
)
API_STATUS = {"new": "open", "in_progress": "in_progress", "closed": "closed"}
DB_STATUS = {v: k for k, v in API_STATUS.items()}


class TicketClosed(Exception):
    """Обращение уже закрыто."""


# --- имена и время -----------------------------------------------------------

def clean_name(full_name: str | None) -> str:
    """ФИО без пометок в скобках: «Тестова Анна (HR, демо)» → «Тестова Анна»."""
    return re.sub(r"\s*[\[(][^\])]*[\])]", "", full_name or "").strip()


def first_name(full_name: str | None) -> str:
    """Имя из «Фамилия Имя Отчество»."""
    parts = clean_name(full_name).split()
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "HR"


def short_name(full_name: str | None) -> str:
    """«Фамилия Имя Отчество» → «Фамилия И.»."""
    parts = clean_name(full_name).split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return parts[0] if parts else ""


def hr_signature(staff) -> str:
    return f"{first_name(getattr(staff, 'full_name', None))} (HR)"


def _tz(emp) -> str:
    # без ленивой загрузки: площадку берём, только если она уже загружена
    site = vars(emp).get("site") if hasattr(emp, "__dict__") else None
    return getattr(site, "tz", None) or settings.tz_default


def _engine_fn(name: str):
    try:
        from app.scenario import engine
    except ImportError:
        return None
    return getattr(engine, name, None)


def employee_day(emp, now: datetime | None = None) -> int:
    """День адаптации: 1 — день выхода, ≤ 0 — до выхода. Считает движок сценария, без него — по календарю."""
    fn = _engine_fn("adaptation_day")
    if fn is not None:
        try:
            return int(fn(emp, now))
        except Exception as e:  # noqa: BLE001 — экран HR не должен падать из-за движка
            log.debug("engine.adaptation_day: %s", e)
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(_tz(emp))) + timedelta(hours=getattr(emp, "time_shift_hours", 0) or 0)
    return (local.date() - emp.start_date).days + 1


def probation_days(emp) -> int:
    fn = _engine_fn("probation_days")
    if fn is not None:
        try:
            return int(fn(emp))
        except Exception as e:  # noqa: BLE001
            log.debug("engine.probation_days: %s", e)
    if getattr(emp, "probation_end", None):
        return max((emp.probation_end - emp.start_date).days, 1)
    return DEFAULT_PROBATION_DAYS


# --- категория и флаг ----------------------------------------------------------

def normalize_category(source: str | None, category: str | None) -> str | None:
    """Категория из списка Mini App. Нераспознанную тему оставляем как есть, её покажем подписью."""
    if category in T.CATEGORY_LABELS:
        return category
    low = (category or "").lower()
    if low:
        if source == "flag":
            # тема флага своими словами → опасная категория; у остальных тем красной метки нет
            for code, hints in _CATEGORY_HINTS:
                if any(h in low for h in hints):
                    return code
        return category[:64]
    return SOURCE_CATEGORY.get(source or "")


def category_of(ticket) -> str:
    return ticket.category or SOURCE_CATEGORY.get(ticket.source) or "question"


def category_label(ticket, topic: str | None = None) -> str:
    cat = category_of(ticket)
    if cat in T.CATEGORY_LABELS:
        if cat == "question" and ticket.is_personal:
            return T.PERSONAL_LABEL
        if cat == "question" and topic:
            return topic[:1].upper() + topic[1:]
        return T.CATEGORY_LABELS[cat]
    if ticket.source == "flag" and not cat:
        return T.FLAG_LABEL
    return cat[:1].upper() + cat[1:]


def is_red(ticket, flag_levels: set[str] | frozenset[str] = frozenset()) -> bool:
    """Красный флаг: опасная категория, красный флаг по обращению или обращение от сигналов (не жёлтый)."""
    if category_of(ticket) in RED_CATEGORIES or "red" in flag_levels:
        return True
    return ticket.source == "flag" and "yellow" not in flag_levels


async def _flag_levels(session, ticket_id: int) -> set[str]:
    if session is None or ticket_id is None:
        return set()
    rows = await session.scalars(select(Flag.level).where(Flag.ticket_id == ticket_id))
    return set(rows)


# --- уведомление в HR-группе ---------------------------------------------------

def _sender(ctx):
    if ctx is not None and getattr(ctx, "sender", None) is not None:
        return ctx.sender
    rt = runtime.get_ctx()
    return rt.sender if rt is not None else None


def _app_button(ticket_id: int) -> dict | None:
    if not settings.max_bot_name:
        return None
    return open_app(T.BTN_APP, settings.max_bot_name.lstrip("@"), payload=f"ticket_{ticket_id}")


def group_buttons(ticket) -> list[dict]:
    """Клавиатура уведомления: «Открыть в Mini App» и, пока обращение новое, «Взять себе»."""
    rows = []
    app = _app_button(ticket.id)
    if app:
        rows.append([app])
    if ticket.status == "new":
        rows.append([callback(T.BTN_TAKE, f"tk:take:{ticket.id}")])
    return keyboard(*rows) if rows else []


def _without_app(atts: list[dict] | None) -> list[dict]:
    """Та же клавиатура без open_app: запасной вариант, если у бота не настроен Mini App."""
    rows = []
    for att in atts or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            row = [b for b in row if b.get("type") != "open_app"]
            if row:
                rows.append(row)
    return keyboard(*rows) if rows else []


async def group_text(session, ticket, emp, *, red: bool | None = None) -> str:
    if red is None:
        red = is_red(ticket, await _flag_levels(session, ticket.id))
    head = (T.RED_HEAD if red else T.HEAD).format(id=ticket.id)
    label = category_label(ticket)
    lines = [f"{head}: {label}", T.WHO.format(name=clean_name(emp.full_name), site=emp.site_code,
                                             day=T.day_label(employee_day(emp)))]
    quote = (ticket.text or "").strip()
    if len(quote) > QUOTE_LIMIT:
        quote = quote[:QUOTE_LIMIT].rstrip() + "…"
    if quote:
        lines += ["", f"«{quote}»"]
    recruiter = await session.get(Staff, emp.recruiter_id) if (session is not None and emp.recruiter_id) else None
    if ticket.status == "new":
        who = T.RECRUITER.format(name=short_name(recruiter.full_name)) if recruiter else T.NO_RECRUITER
        status_line = f"{T.sentence(who)} {T.STATUS['new']}"
    else:
        who = await session.get(Staff, ticket.assignee_id) if (session is not None and ticket.assignee_id) else None
        status_line = T.sentence(T.STATUS[ticket.status if ticket.status in T.STATUS else "in_progress"].format(
            name=short_name(who.full_name) if who else "HR"))
    lines += ["", status_line]
    return "\n".join(lines)


async def _send(sender, repo, text: str, atts: list[dict], *, chat_id=None, staff=None) -> list[str] | None:
    """Отправить с кнопками; если «МАКС» не принял open_app — без неё."""
    if sender is None:
        return None
    variants = [atts]
    plain = _without_app(atts)
    if plain != atts:
        variants.append(plain)
    for i, variant in enumerate(variants):
        try:
            if chat_id is not None:
                return await sender.send(repo, text, chat_id=chat_id, attachments=variant or None)
            return await sender.send(repo, text, chat_id=staff.chat_id, user_id=staff.max_user_id,
                                     attachments=variant or None, staff=staff)
        except Exception as e:  # noqa: BLE001 — уведомление не должно ломать обращение
            log.warning("Уведомление об обращении не отправлено%s: %s",
                        "" if i == len(variants) - 1 else ", пробуем без кнопки Mini App", e)
    return None


async def notify_staff(ctx, repo, emp, text: str, atts: list[dict]) -> list[str] | None:
    """HR-группа; если её нет — закреплённый рекрутер лично."""
    sender = _sender(ctx)
    if sender is None:
        log.info("Бот не запущен, уведомление HR пропущено")
        return None
    chat_id = await repo.get_setting("hr_group_chat_id")
    if chat_id:
        mids = await _send(sender, repo, text, atts, chat_id=int(chat_id))
        if mids:
            return mids
    session = getattr(repo, "s", None)
    recruiter = await session.get(Staff, emp.recruiter_id) if (session is not None and emp.recruiter_id) else None
    if recruiter is not None and (recruiter.chat_id or recruiter.max_user_id):
        return await _send(sender, repo, text, atts, staff=recruiter)
    log.info("HR-группа не подключена и рекрутер не в боте: уведомление об обращении пропущено")
    return None


async def refresh_group(session, ticket, emp=None) -> bool:
    """Поправить уведомление об обращении на месте: статус и кнопки."""
    if not ticket.group_mid:
        return False
    sender = _sender(None)
    if sender is None:
        return False
    emp = emp or await session.get(Employee, ticket.employee_id)
    text = await group_text(session, ticket, emp)
    atts = group_buttons(ticket)
    if await sender.edit(ticket.group_mid, text, attachments=atts):
        return True
    plain = _without_app(atts)
    return plain != atts and await sender.edit(ticket.group_mid, text, attachments=plain)


# --- создание -----------------------------------------------------------------

async def _link_question(session, emp, ticket) -> None:
    """Сообщение новичка с текстом обращения → в ленту обращения."""
    since = datetime.now(UTC) - timedelta(hours=1)
    msg = await session.scalar(
        select(Message)
        .where(Message.employee_id == emp.id, Message.direction == "in", Message.ticket_id.is_(None),
               Message.text == (ticket.text or ""), Message.created_at >= since)
        .order_by(Message.id.desc())
        .limit(1)
    )
    if msg is not None:
        msg.ticket_id = ticket.id


async def create_ticket(ctx, repo, emp, text: str, *, source: str, category: str | None = None,
                        is_personal: bool = False, level: str | None = None, **_kw) -> Ticket:
    """Фабрика обращений (hooks.on_create_ticket). Сообщение новичку пишет вызывающий код.

    level — уровень флага от модуля сигналов ("red" | "yellow"), если известен заранее.
    """
    cat = normalize_category(source, category)
    ticket = await repo.create_ticket(employee_id=emp.id, source=source, text=text or "",
                                      assignee_id=getattr(emp, "recruiter_id", None), category=cat,
                                      is_personal=is_personal)
    session = getattr(repo, "s", None)
    if session is not None:
        await _link_question(session, emp, ticket)
    try:
        red = is_red(ticket, {level} if level else set())
        body = await group_text(session, ticket, emp, red=red)
        mids = await notify_staff(ctx, repo, emp, body, group_buttons(ticket))
        if mids:
            ticket.group_mid = mids[-1]
    except Exception:
        log.exception("Обращение #%s: уведомление HR не отправлено", ticket.id)
    if hasattr(repo, "flush"):
        await repo.flush()
    log.info("Обращение #%s: новичок %s, источник %s, категория %s", ticket.id, emp.id, source, cat)
    return ticket


# --- окно ответа ----------------------------------------------------------------

def _set_window(emp, ticket_id: int | None, until: datetime | None = None) -> None:
    data = dict(emp.dialog_data or {})
    if ticket_id is None:
        data.pop(WINDOW_KEY, None)
    else:
        data[WINDOW_KEY] = {"ticket_id": ticket_id, "until": until.isoformat(), "relayed": False}
    emp.dialog_data = data  # новый dict, чтобы SQLAlchemy заметил изменение JSONB


def open_window(emp) -> dict | None:
    """Открытое окно ответа HR или None."""
    w = (emp.dialog_data or {}).get(WINDOW_KEY)
    if not isinstance(w, dict) or not w.get("ticket_id"):
        return None
    try:
        until = datetime.fromisoformat(w["until"])
    except (KeyError, TypeError, ValueError):
        return None
    return w if datetime.now(UTC) <= until else None


async def route_reply(ctx, repo, emp, text: str, msg: dict) -> bool:
    """pre_route: текст новичка в течение 30 минут после ответа HR → в то же обращение."""
    w = open_window(emp)
    if w is None:
        if (emp.dialog_data or {}).get(WINDOW_KEY):
            _set_window(emp, None)
        return False
    session = repo.s
    ticket = await session.get(Ticket, int(w["ticket_id"]))
    if ticket is None or ticket.status == "closed":
        _set_window(emp, None)
        return False
    has_files = bool((msg or {}).get("attachments"))
    if not text and not has_files:
        return False

    # входящее сообщение уже в журнале (его пишет бот) — привязываем к обращению
    mid = (msg or {}).get("mid")
    q = select(Message).where(Message.employee_id == emp.id, Message.direction == "in",
                              Message.ticket_id.is_(None))
    q = q.where(Message.mid == mid) if mid else q
    last = await session.scalar(q.order_by(Message.id.desc()).limit(1))
    if last is not None:
        last.ticket_id = ticket.id
    else:
        await repo.add_message(employee_id=emp.id, staff_id=None, chat_id=emp.chat_id, direction="in",
                               text=text, mid=mid, ticket_id=ticket.id)

    quote = text or T.ATTACHMENT
    if len(quote) > QUOTE_LIMIT:
        quote = quote[:QUOTE_LIMIT].rstrip() + "…"
    note = T.FOLLOW_UP.format(id=ticket.id, name=clean_name(emp.full_name), site=emp.site_code, text=quote)
    app = _app_button(ticket.id)
    await notify_staff(ctx, repo, emp, note, keyboard([app]) if app else [])

    if not w.get("relayed"):
        # одно подтверждение на ответ HR, дальше молча передаём
        data = dict(emp.dialog_data or {})
        data[WINDOW_KEY] = {**w, "relayed": True}
        emp.dialog_data = data
        sender = _sender(ctx)
        if sender is not None:
            await sender.send(repo, tv(emp, T.RELAYED), chat_id=emp.chat_id, user_id=emp.max_user_id,
                              employee=emp, ticket_id=ticket.id)
    return True


# --- действия HR ------------------------------------------------------------------

async def reply(session, ticket: Ticket, staff, text: str, *, add_to_kb: bool = False,
                kb_site: str | None = None) -> Ticket:
    """Ответ HR новичку в чат «МАКС» с подписью «Имя (HR)».

    Первый ответ переводит обращение в работу и назначает ответившего. add_to_kb — черновик ответа HR
    (HrAnswer, status="draft"); личные обращения в базу не попадают.
    Не доставлено (бот выключен, новичок не подключён) — ответ всё равно в ленте обращения, без mid.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Пустой ответ")
    if ticket.status == "closed":
        raise TicketClosed()
    emp = await session.get(Employee, ticket.employee_id)
    first = ticket.first_response_at is None
    body = f"**{hr_signature(staff)}**\n{text}"
    if first:
        body += "\n\n" + tv(emp, T.REPLY_HINT)

    mids = None
    if emp.chat_id or emp.max_user_id:
        try:
            # repo=None: в журнал пишем сами, без подписи и с автором-HR
            mids = await runtime.send(None, body, employee=emp)
        except Exception as e:  # noqa: BLE001 — ответ сохраняем, даже если «МАКС» не принял
            log.warning("Ответ HR по обращению #%s не доставлен: %s", ticket.id, e)
    session.add(Message(employee_id=emp.id, staff_id=staff.id, chat_id=emp.chat_id, direction="out", text=text,
                        mid=(mids or [None])[0], ticket_id=ticket.id))

    now = datetime.now(UTC)
    if first:
        ticket.first_response_at = now
    if ticket.status == "new":
        ticket.status = "in_progress"
        ticket.assignee_id = staff.id
    elif ticket.assignee_id is None:
        ticket.assignee_id = staff.id
    if mids:
        _set_window(emp, ticket.id, now + REPLY_WINDOW)
    if add_to_kb and not ticket.is_personal:
        site = None if kb_site in (None, "", "all") else kb_site
        session.add(HrAnswer(question=ticket.text or "", answer=text, site_code=site, author_id=staff.id,
                             status="draft", ticket_id=ticket.id))
    await session.flush()
    await refresh_group(session, ticket, emp)
    return ticket


async def take(session, ticket: Ticket, staff, *, edit_group: bool = True) -> Ticket:
    if ticket.status == "closed":
        raise TicketClosed()
    ticket.status = "in_progress"
    ticket.assignee_id = staff.id
    await session.flush()
    if edit_group:
        await refresh_group(session, ticket)
    return ticket


async def close(session, ticket: Ticket, staff) -> Ticket:
    """Закрыть обращение: окно ответа закрывается, флаги по обращению — «разобраны»."""
    if ticket.status != "closed":
        ticket.status = "closed"
        ticket.closed_at = datetime.now(UTC)
        if ticket.assignee_id is None:
            ticket.assignee_id = staff.id
    emp = await session.get(Employee, ticket.employee_id)
    w = (emp.dialog_data or {}).get(WINDOW_KEY)
    if isinstance(w, dict) and w.get("ticket_id") == ticket.id:
        _set_window(emp, None)
    await session.execute(update(Flag).where(Flag.ticket_id == ticket.id, Flag.status != "resolved")
                          .values(status="resolved"))
    await session.flush()
    await refresh_group(session, ticket, emp)
    return ticket
