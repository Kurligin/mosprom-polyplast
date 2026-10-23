"""Сигналы из ответов новичков: флаги по словарю, обращения HR, ИИ-разбор свободных ответов.

- check_text: словарь флагов работает всегда; ИИ-разбор (тема, настроение, цитата) — только при согласии
  ai_analysis и не для текстов о здоровье. Разбор идёт в фоне и дописывает SurveyAnswer.
- raise_flag: запись Flag; красный флаг → обращение source="flag" (уведомление в HR-группу делает фабрика
  обращений) и ссылка на него во флаге. Жёлтый флаг — только отметка для карточки и сводки.
- employee_notice: что сказать новичку после красного флага: коротко, серьёзно, без эмодзи.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.bot import hooks, runtime
from app.bot.keyboards import keyboard, open_app
from app.bot.texts import tv
from app.config import settings
from app.db.models import Consent, Flag, Message, Site, SurveyAnswer, Ticket
from app.privacy import is_health
from app.signals import rules as R

log = logging.getLogger(__name__)

# Тема флага → категория обращения (app/tickets: safety, harassment, intent_to_leave, health)
TICKET_CATEGORY = {
    "безопасность": "safety",
    "охрана труда": "safety",
    "травля": "harassment",
    "угрозы": "harassment",
    "здоровье": "health",
    "намерение уйти": "intent_to_leave",
}
MOOD_EN = {"негатив": "negative", "нейтрально": "neutral", "позитив": "positive"}
QUOTE_LIMIT = 1000
DEDUP_WINDOW = timedelta(hours=12)   # тот же флаг у того же новичка не дублируем
NIGHT = (21, 9)                      # с 21:00 до 09:00 по времени площадки добавляем 112

NOTICE = (
    "Это важно. Я сразу передал твоё сообщение HR, его увидит только HR.",
    "Это важно. Я сразу передал ваше сообщение HR, его увидит только HR.",
)
EMERGENCY = (
    "Если нужна срочная помощь, звони 112.",
    "Если нужна срочная помощь, звоните 112.",
)
GROUP_FLAG = "🔴 Красный флаг: {topic}\n{name}, {site}\n\n«{quote}»"

# Фоновый ИИ-разбор: можно выключить в тестах
AI_ENABLED = True
_tasks: set[asyncio.Task] = set()


# --- флаги -----------------------------------------------------------------------

async def _create_ticket(repo, emp, text: str, *, category: str, level: str):
    """Обращение через фабрику модуля обращений; если её нет — запись и уведомление в HR-группу сами."""
    ctx = runtime.get_ctx()
    factory = hooks.single("create_ticket")
    if factory is not None:
        try:
            return await factory(ctx, repo, emp, text, source="flag", category=category, level=level)
        except TypeError:
            return await factory(ctx, repo, emp, text, source="flag", category=category)
    ticket = await repo.create_ticket(employee_id=emp.id, source="flag", text=text,
                                      assignee_id=getattr(emp, "recruiter_id", None), category=category)
    tid = getattr(ticket, "id", None)
    atts = keyboard([open_app("Открыть в Mini App", settings.max_bot_name.lstrip("@"),
                              payload=f"ticket_{tid}" if tid else None)]) if settings.max_bot_name else None
    await runtime.notify_hr_group(repo, GROUP_FLAG.format(topic=category, name=emp.full_name,
                                                          site=emp.site_code, quote=text[:QUOTE_LIMIT]), atts)
    return ticket


async def _follow_up(repo, emp, flag: Flag, text: str) -> bool:
    """Повтор той же тревоги свободным сообщением: оно уходит в открытое обращение флага, HR узнаёт о нём.

    False — обращения нет или оно закрыто: тогда нужен новый флаг и новое обращение.
    """
    from app.tickets import service as tickets
    from app.tickets import texts as TT

    session = repo.s
    ticket = await session.get(Ticket, flag.ticket_id) if flag.ticket_id else None
    if ticket is None or ticket.status == "closed":
        return False
    # входящее сообщение уже в журнале (его пишет бот) — в ленту обращения
    msg = await session.scalar(
        select(Message).where(Message.employee_id == emp.id, Message.direction == "in",
                              Message.ticket_id.is_(None), Message.text == text)
        .order_by(Message.id.desc()).limit(1))
    if msg is not None:
        msg.ticket_id = ticket.id
    quote = text if len(text) <= tickets.QUOTE_LIMIT else text[:tickets.QUOTE_LIMIT].rstrip() + "…"
    note = TT.FOLLOW_UP.format(id=ticket.id, name=tickets.clean_name(emp.full_name), site=emp.site_code,
                               text=quote)
    app = tickets._app_button(ticket.id)
    try:
        await tickets.notify_staff(None, repo, emp, note, keyboard([app]) if app else [])
    except Exception:  # обращение и привязка сообщения важнее уведомления
        log.exception("Флаг #%s: HR не узнал о повторном сообщении", flag.id)
    await session.flush()
    log.info("Флаг #%s: повторное сообщение новичка %s ушло в обращение #%s", flag.id, emp.id, ticket.id)
    return True


async def raise_flag(repo, emp, *, level: str, topic: str, quote: str | None, source: str,
                     answer=None, ticket_id: int | None = None) -> Flag:
    """Флаг новичку. Красный → обращение HR (source="flag") и уведомление в HR-группу.

    ticket_id — привязать к уже созданному обращению (несколько флагов из одного текста — одно обращение).
    Та же тема за 12 часов новый флаг не создаёт. Повтор красного флага свободным сообщением уходит
    в открытое обращение этого флага (и HR получает уведомление); обращение закрыто — новое обращение.
    """
    session = repo.s
    since = datetime.now(UTC) - DEDUP_WINDOW
    q = select(Flag).where(Flag.employee_id == emp.id, Flag.topic == topic, Flag.level == level,
                           Flag.created_at >= since, Flag.status == "new")
    if answer is not None:
        q = q.where(Flag.answer_id == answer.id)
    existing = (await session.scalars(q.order_by(Flag.id.desc()).limit(1))).first()
    if existing is not None:
        repeat = level == "red" and answer is None and ticket_id is None and bool((quote or "").strip())
        if not repeat or await _follow_up(repo, emp, existing, (quote or "").strip()):
            return existing

    flag = Flag(employee_id=emp.id, level=level, topic=topic[:64], quote=(quote or "")[:QUOTE_LIMIT] or None,
                source=source[:16], answer_id=getattr(answer, "id", None), ticket_id=ticket_id, status="new")
    session.add(flag)
    await session.flush()
    if level == "red" and ticket_id is None:
        text = (quote or topic)[:QUOTE_LIMIT]
        try:
            ticket = await _create_ticket(repo, emp, text, category=TICKET_CATEGORY.get(topic, topic), level=level)
            flag.ticket_id = getattr(ticket, "id", None)
            await session.flush()
        except Exception:  # флаг уже записан, HR увидит его в Mini App
            log.exception("Флаг #%s: не удалось создать обращение", flag.id)
    log.info("Флаг #%s %s «%s» у новичка %s (%s)", flag.id, level, topic, emp.id, source)
    return flag


async def raise_hits(repo, emp, hits: list[R.Hit], text: str, *, source: str, answer=None) -> list[Flag]:
    """Флаги по срабатываниям словаря. Красные из одного текста — одно обращение (по самой серьёзной теме)."""
    flags: list[Flag] = []
    ticket_id = None
    for h in hits:
        flag = await raise_flag(repo, emp, level=h.level, topic=h.topic, quote=text.strip(), source=source,
                                answer=answer, ticket_id=ticket_id if h.level == "red" else None)
        if h.level == "red" and ticket_id is None:
            ticket_id = flag.ticket_id
        flag.hit = h  # для employee_notice: не в БД
        flags.append(flag)
    return flags


async def check_text(repo, emp, text: str, *, source: str, answer=None) -> list[Flag]:
    """Свободный текст новичка → флаги по словарю. Для ответа опроса — ещё и ИИ-разбор (с согласием)."""
    if not text or not text.strip():
        return []
    hits = R.detect(text)
    flags = await raise_hits(repo, emp, hits, text, source=source, answer=answer)
    if answer is not None:
        if any(h.category == "health" for h in hits) or R.is_sensitive(text) or is_health(text):
            log.info("Ответ #%s: тема здоровья, в LLM не отправляем", answer.id)
        elif await has_ai_consent(repo, emp):
            schedule_analysis(answer.id, text, _names(emp), emp.id)
    return flags


def notice_needed(flags: list[Flag]) -> bool:
    return any(f.level == "red" and getattr(getattr(f, "hit", None), "notice", True) for f in flags)


async def is_night(repo, emp, now: datetime | None = None) -> bool:
    """Ночь по реальному времени площадки (машина времени не влияет: читает живой человек)."""
    tz = settings.tz_default
    session = getattr(repo, "s", None)
    if session is not None:
        site = await session.get(Site, emp.site_code)
        tz = site.tz if site and site.tz else tz
    hour = (now or datetime.now(UTC)).astimezone(ZoneInfo(tz)).hour
    return hour >= NIGHT[0] or hour < NIGHT[1]


async def employee_notice(repo, emp, flags: list[Flag], now: datetime | None = None) -> str | None:
    """«Передал HR, увидит только HR» (+ 112 ночью и для угроз и здоровья) или None."""
    if not notice_needed(flags):
        return None
    text = tv(emp, NOTICE)
    emergency = any(getattr(getattr(f, "hit", None), "emergency", False) for f in flags)
    if emergency or await is_night(repo, emp, now):
        text += "\n\n" + tv(emp, EMERGENCY)
    return text


# --- ИИ-разбор ---------------------------------------------------------------------

async def has_ai_consent(repo, emp) -> bool:
    """Последнее решение новичка по согласию ai_analysis."""
    row = (await repo.s.scalars(
        select(Consent).where(Consent.employee_id == emp.id, Consent.purpose == "ai_analysis")
        .order_by(Consent.given_at.desc(), Consent.id.desc()).limit(1))).first()
    return bool(row and row.given)


def _names(emp) -> list[str]:
    return [n for n in (getattr(emp, "full_name", None), getattr(emp, "first_name", None)) if n]


def schedule_analysis(answer_id: int, text: str, names: list[str], employee_id: int) -> None:
    """Разбор в фоне: GigaChat думает секунды, новичок не должен ждать следующий вопрос."""
    if not AI_ENABLED:
        return
    try:
        task = asyncio.get_running_loop().create_task(analyze_answer_later(answer_id, text, names, employee_id))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def analyze_answer_later(answer_id: int, text: str, names: list[str], employee_id: int) -> dict | None:
    """GigaChat → topic / mood / quote в SurveyAnswer. Ответ ищем с повтором: транзакция бота могла ещё не закрыться."""
    from app.db.models import Employee
    from app.db.session import async_session
    from app.llm import gigachat

    try:
        result = await gigachat.analyze_answer(text, names)
    except Exception as e:  # noqa: BLE001
        log.warning("ИИ-разбор ответа #%s: %s", answer_id, type(e).__name__)
        return None
    if not result:
        return None
    for _ in range(10):
        async with async_session() as s:
            ans = await s.get(SurveyAnswer, answer_id)
            if ans is None:
                await asyncio.sleep(1)
                continue
            ans.topic = result["topic"][:64]
            ans.mood = MOOD_EN.get(result["mood"], "neutral")
            ans.evidence_quote = result.get("quote")
            # Охрана труда с негативом, которую словарь не поймал → жёлтый флаг «ИИ заметил»
            if result["topic"] == "охрана труда" and result["mood"] == "негатив":
                has_red = await s.scalar(select(Flag.id).where(Flag.answer_id == answer_id, Flag.level == "red"))
                emp = await s.get(Employee, employee_id)
                if not has_red and emp is not None:
                    from app.bot.repo import SqlRepo

                    await raise_flag(SqlRepo(s), emp, level="yellow", topic="охрана труда",
                                     quote=result.get("quote") or text, source="llm", answer=ans)
            await s.commit()
            log.info("ИИ-разбор ответа #%s: тема %s, настроение %s", answer_id, ans.topic, ans.mood)
            return result
    log.warning("ИИ-разбор: ответ #%s не найден", answer_id)
    return None
