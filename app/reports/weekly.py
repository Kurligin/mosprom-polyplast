"""Еженедельная сводка: агрегаты за 4 недели по площадкам.

Приватность:
- срез по площадке — только если ответили 5 и больше разных новичков;
- площадки меньше порога объединяются в одну строку с пометкой «мало данных»,
  если и вместе меньше порога — цифр по ним нет;
- порог проверяем для каждой цифры отдельно: средняя оценка — от 5 новичков, поставивших оценку,
  настроение — от 5 новичков, у чьих ответов оно есть; иначе вместо цифры «мало ответов»;
- общие оценки, настроение и темы — тоже только от 5 ответивших;
- имён, ответов и цитат в сводке нет: только числа.

Открытые сигналы — все незакрытые флаги, сколько бы недель назад их ни подняли; закрытые — за окно 4 недели.
Отправка — понедельник, 09:00 МСК. Если приложение в это время не работало, задача догона отправит сводку
за прошедшую неделю позже: отправленная неделя хранится в `Setting` (`SENT_KEY`).

Настроение ответа — `SurveyAnswer.mood` (ИИ-разбор), если его нет — по оценке: 8–10 хорошо, 5–7 так себе, 1–4 плохо.
Темы — `SurveyAnswer.topic` и `QuestionLog.topic`, без «другое». Из ответов опросов берём только текстовые
и не позитивные: тема кнопки — это тема вопроса, иначе топ повторял бы набор вопросов опроса.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app.config import settings
from app.db.models import (
    Employee,
    Flag,
    QuestionLog,
    Setting,
    Site,
    Staff,
    SurveyAnswer,
)

log = logging.getLogger(__name__)

THRESHOLD = 5
WINDOW_WEEKS = 4
SEND_AT = time(9, 0)                  # понедельник, 09:00 МСК
SENT_KEY = "weekly_report_sent_week"  # Setting: воскресенье последней отправленной недели (ISO)
TOP_TOPICS = 3
MOODS = ("positive", "neutral", "negative")
_MOOD_ALIASES = {
    "positive": "positive", "позитив": "positive", "позитивное": "positive",
    "neutral": "neutral", "нейтрально": "neutral", "нейтральное": "neutral",
    "negative": "negative", "негатив": "negative", "негативное": "negative",
}
_SKIP_TOPICS = {"", "другое", "other", "none"}
_sleep = asyncio.sleep  # подменяется в тестах
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря")


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.tz_default)


# --- подписи дат -------------------------------------------------------------

def range_label(a: date, b: date, *, year: bool = False) -> str:
    """«13–19 октября», «28 сентября – 4 октября», с годом — «13–19 октября 2026»."""
    if a.month == b.month and a.year == b.year:
        s = f"{a.day}–{b.day} {MONTHS_GEN[b.month - 1]}"
    else:
        s = f"{a.day} {MONTHS_GEN[a.month - 1]} – {b.day} {MONTHS_GEN[b.month - 1]}"
    return f"{s} {b.year}" if year else s


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def period(now: datetime | None = None, *, week_end: date | None = None) -> dict:
    """Неделя сводки и окно в 4 недели. В понедельник неделя — прошедшая (пн–вс), в другие дни — 7 дней по сегодня.

    week_end — последний день недели явно (догон пропущенной сводки).
    """
    if week_end is None:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        local = now.astimezone(_tz())
        end = local.date() - timedelta(days=1) if local.weekday() == 0 else local.date()
    else:
        end = week_end
    week_start = end - timedelta(days=6)
    window_start = end - timedelta(days=WINDOW_WEEKS * 7 - 1)
    return {
        "week_start": week_start,
        "week_end": end,
        "window_start": window_start,
        "from_dt": datetime.combine(window_start, time(0), tzinfo=_tz()),
        "to_dt": datetime.combine(end + timedelta(days=1), time(0), tzinfo=_tz()),
    }


# --- сырые данные ------------------------------------------------------------

@dataclass
class Raw:
    sites: list[tuple[str, str]] = field(default_factory=list)   # (code, name)
    employees: list[dict] = field(default_factory=list)          # id, site, start_date, probation_end, status, synthetic
    answers: list[dict] = field(default_factory=list)            # employee_id, site, score, mood, topic, text, synthetic
    questions: list[dict] = field(default_factory=list)          # employee_id, site, topic
    flags: list[dict] = field(default_factory=list)              # employee_id, site, status, level


async def collect(session, p: dict) -> Raw:
    lo, hi = p["from_dt"], p["to_dt"]
    raw = Raw()
    raw.sites = [(s.code, s.name) for s in (await session.scalars(select(Site).order_by(Site.code))).all()]
    for e in (await session.execute(select(
            Employee.id, Employee.site_code, Employee.start_date, Employee.probation_end, Employee.status,
            Employee.is_synthetic))).all():
        raw.employees.append({"id": e[0], "site": e[1], "start_date": e[2], "probation_end": e[3],
                              "status": e[4], "synthetic": bool(e[5])})
    rows = await session.execute(
        select(SurveyAnswer.employee_id, Employee.site_code, SurveyAnswer.score, SurveyAnswer.mood,
               SurveyAnswer.topic, SurveyAnswer.is_synthetic, Employee.is_synthetic,
               func.length(func.coalesce(func.trim(SurveyAnswer.text), "")) > 0)
        .join(Employee, Employee.id == SurveyAnswer.employee_id)
        .where(SurveyAnswer.created_at >= lo, SurveyAnswer.created_at < hi))
    raw.answers = [{"employee_id": r[0], "site": r[1], "score": r[2], "mood": r[3], "topic": r[4],
                    "text": bool(r[7]), "synthetic": bool(r[5] or r[6])} for r in rows.all()]
    rows = await session.execute(
        select(QuestionLog.employee_id, Employee.site_code, QuestionLog.topic)
        .join(Employee, Employee.id == QuestionLog.employee_id)
        .where(QuestionLog.created_at >= lo, QuestionLog.created_at < hi, QuestionLog.topic.is_not(None)))
    raw.questions = [{"employee_id": r[0], "site": r[1], "topic": r[2]} for r in rows.all()]
    # открытые — все незакрытые, сколько бы недель назад их ни подняли; закрытые — только за окно
    rows = await session.execute(
        select(Flag.employee_id, Employee.site_code, Flag.status, Flag.level)
        .join(Employee, Employee.id == Flag.employee_id)
        .where(Flag.created_at < hi, or_(Flag.created_at >= lo, Flag.status != "resolved")))
    raw.flags = [{"employee_id": r[0], "site": r[1], "status": r[2], "level": r[3]} for r in rows.all()]
    return raw


# --- агрегаты (чистые функции) ----------------------------------------------

def mood_of(mood: str | None, score: int | None) -> str | None:
    m = _MOOD_ALIASES.get((mood or "").strip().lower())
    if m:
        return m
    if score is None:
        return None
    return "positive" if score >= 8 else "neutral" if score >= 5 else "negative"


def topic_label(topic: str) -> str:
    t = topic.strip().replace("_", " ")
    return t[:1].upper() + t[1:]


def _in_adaptation(e: dict, end: date) -> bool:
    if e["status"] == "finished" or e["start_date"] > end:
        return False
    return e["probation_end"] is None or e["probation_end"] >= end


def _in_window(e: dict, lo: date, end: date) -> bool:
    """Был в адаптации хотя бы день из окна 4 недель: мог ответить на опрос этого окна."""
    if e["start_date"] > end:
        return False
    return e["probation_end"] is None or e["probation_end"] >= lo


def _slice(answers: list[dict], threshold: int = THRESHOLD) -> dict:
    """Оценки и настроение по набору ответов.

    Порог — для каждой цифры свой: средняя оценка — только если оценку поставили threshold разных новичков,
    настроение — только если оно есть в ответах threshold разных новичков. Иначе None и пустые счётчики.
    """
    scored = [a for a in answers if a["score"] is not None]
    moods = [(a["employee_id"], m) for a in answers if (m := mood_of(a["mood"], a["score"]))]
    score_people = len({a["employee_id"] for a in scored})
    mood_people = len({eid for eid, _ in moods})
    counts = Counter(m for _, m in moods) if mood_people >= threshold else Counter()
    total = sum(counts.values())
    share = {k: (counts[k] / total if total else None) for k in MOODS}
    avg = round(sum(a["score"] for a in scored) / len(scored), 1) if scored and score_people >= threshold else None
    return {
        "respondents": len({a["employee_id"] for a in answers}),
        "answers": len(answers),
        "scores": len(scored),
        "score_respondents": score_people,
        "mood_respondents": mood_people,
        "avg_score": avg,
        "mood": {k: counts[k] for k in MOODS},
        "mood_total": total,
        "mood_share": share,
    }


def topic_worthy(answer: dict) -> bool:
    """Ответ опроса говорит о теме: свой текст или не позитивный ответ (кнопка «всё отлично» — нет)."""
    if answer.get("text"):
        return True
    return mood_of(answer.get("mood"), answer.get("score")) in ("neutral", "negative")


def _topics(answers: list[dict], questions: list[dict]) -> list[dict]:
    """Топ тем: текстовые и не позитивные ответы опросов + вопросы боту (questions_log)."""
    c: Counter[str] = Counter()
    for row in (*(a for a in answers if topic_worthy(a)), *questions):
        t = (row.get("topic") or "").strip()
        if t.lower() not in _SKIP_TOPICS:
            c[topic_label(t)] += 1
    return [{"topic": t, "count": n} for t, n in c.most_common(TOP_TOPICS)]


def aggregate(raw: Raw, p: dict, *, threshold: int = THRESHOLD) -> dict:
    end, week_start = p["week_end"], p["week_start"]
    names = dict(raw.sites)
    in_adapt = [e for e in raw.employees if _in_adaptation(e, end)]
    in_window = [e for e in raw.employees if _in_window(e, p["window_start"], end)]

    codes = [c for c, _ in raw.sites]
    for row in (*in_adapt, *raw.answers):
        if row["site"] not in codes:
            codes.append(row["site"])

    sites, small = [], []
    for code in codes:
        answers = [a for a in raw.answers if a["site"] == code]
        # «N из M ответили»: M — кто был в адаптации за 4 недели (и все ответившие), иначе N > M
        newbies = len({e["id"] for e in in_window if e["site"] == code} | {a["employee_id"] for a in answers})
        if not newbies and not answers:
            continue
        s = {"code": code, "name": names.get(code, code), "newbies": newbies, **_slice(answers, threshold)}
        (sites if s["respondents"] >= threshold else small).append(s)

    merged = None
    if small:
        small_codes = {s["code"] for s in small}
        answers = [a for a in raw.answers if a["site"] in small_codes]
        merged = {"codes": [s["code"] for s in small], "names": [s["name"] for s in small],
                  "newbies": sum(s["newbies"] for s in small), **_slice(answers, threshold)}
        merged["shown"] = merged["respondents"] >= threshold
        merged["name"] = "Остальные площадки" if len(small) > 1 else small[0]["name"]

    overall = _slice(raw.answers, threshold)
    enough = overall["respondents"] >= threshold
    flags_open = [f for f in raw.flags if f["status"] != "resolved"]
    topics = _topics(raw.answers, raw.questions) if enough else []

    # для каждого показанного среза — свои топ-темы (в картинке не выводятся, нужны Mini App)
    for s in sites:
        s["topics"] = _topics([a for a in raw.answers if a["site"] == s["code"]],
                              [q for q in raw.questions if q["site"] == s["code"]])

    rows = [dict(s, low_data=False) for s in sites]
    if merged and merged["shown"]:
        rows.append({"code": "merged", "name": merged["name"], "newbies": merged["newbies"], "low_data": True,
                     **{k: merged[k] for k in ("respondents", "answers", "scores", "score_respondents",
                                               "mood_respondents", "avg_score", "mood", "mood_total",
                                               "mood_share")}})
    rows.sort(key=lambda r: (r["low_data"], -r["respondents"]))

    synthetic = any(a["synthetic"] for a in raw.answers) or any(e["synthetic"] for e in in_adapt)
    return {
        "threshold": threshold,
        "week": {"start": week_start.isoformat(), "end": end.isoformat(),
                 "label": range_label(week_start, end, year=True)},
        "window": {"start": p["window_start"].isoformat(), "end": end.isoformat(),
                   "label": range_label(p["window_start"], end)},
        "kpi": {
            "newbies": len(in_adapt),
            "newbies_week": sum(1 for e in in_adapt if week_start <= e["start_date"] <= end),
            "respondents": overall["respondents"],
            "answers": overall["answers"],
            "avg_score": overall["avg_score"] if enough else None,
            "scores": overall["scores"],
            "score_respondents": overall["score_respondents"],
            "signals_open": len(flags_open),
            "signals_red_open": sum(1 for f in flags_open if f["level"] == "red"),
            "signals_closed": len(raw.flags) - len(flags_open),
        },
        "overall": {**overall, "shown": enough},
        "sites": sites,
        "merged": merged,
        "mood_rows": rows,
        "low_data": [] if merged is None or merged["shown"] else merged["names"],
        "topics": topics,
        "synthetic": synthetic,
    }


async def build(session, now: datetime | None = None, *, week_end: date | None = None) -> dict:
    p = period(now, week_end=week_end)
    report = aggregate(await collect(session, p), p)
    report["generated_at"] = (now or datetime.now(UTC)).isoformat()
    return report


# --- отправка ---------------------------------------------------------------

def _greeting(now: datetime) -> str:
    h = now.astimezone(_tz()).hour
    return "Доброе утро" if 5 <= h < 12 else "Добрый день" if h < 18 else "Добрый вечер"


def _num(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def caption(report: dict, now: datetime | None = None) -> str:
    """Короткая подпись к картинке: главное числами, на случай если картинка не загрузится."""
    k = report["kpi"]
    n = k["newbies"]
    lines = [f"{_greeting(now or datetime.now(UTC))}! Сводка по новичкам за {report['week']['label']}."]
    parts = [f"В адаптации {n} {plural(n, ('человек', 'человека', 'человек'))}"]
    if k["avg_score"] is not None:
        parts.append(f"средняя оценка {_num(k['avg_score'])} из 10")
    parts.append(f"открытых сигналов: {k['signals_open']}")
    lines.append(", ".join(parts) + ".")
    if report.get("synthetic"):
        lines.append("Данные синтетические.")
    return "\n".join(lines)


async def _send_image(repo, text: str, staff, atts: list[dict]):
    """«МАКС» обрабатывает картинку после загрузки: attachment.not.ready — ждём и повторяем."""
    from app.bot import runtime
    from app.bot.api import MaxApiError

    for attempt in range(5):
        try:
            return await runtime.send(repo, text, staff=staff, attachments=atts)
        except MaxApiError as e:
            if "not.ready" not in f"{e.code} {e.message}" or attempt == 4:
                raise
            await _sleep(1 + attempt)
    return None


async def send(session, now: datetime | None = None, *, week_end: date | None = None) -> bool:
    """Картинка сводки руководителю HR (`Staff.role == "head_recruitment"`, привязан к «МАКС»).

    week_end — неделя сводки явно (догон), иначе — по `period(now)`.
    """
    from app.bot import runtime
    from app.bot.keyboards import keyboard, open_app
    from app.bot.repo import SqlRepo
    from app.reports import render

    heads = [h for h in (await session.scalars(select(Staff).where(Staff.role == "head_recruitment"))).all()
             if h.chat_id or h.max_user_id]
    if not heads:
        log.info("Сводка: руководитель HR не подключён к боту")
        return False
    ctx = runtime.get_ctx()
    if ctx is None:
        log.info("Сводка: бот не запущен")
        return False

    report = await build(session, now, week_end=week_end)
    png = await render.render_png(report)
    payload = await ctx.sender.call(None, ctx.sender.api.upload, "image", "korsik-weekly.png", png, "image/png")
    atts = [{"type": "image", "payload": payload}]
    if settings.miniapp_url and settings.max_bot_name:
        atts += keyboard([open_app("Открыть в Mini App", settings.max_bot_name.lstrip("@"))])

    repo = SqlRepo(session)
    text = caption(report, now)
    ok = False
    for head in heads:
        try:
            ok = bool(await _send_image(repo, text, head, atts)) or ok
        except Exception:  # одному не ушло, остальным отправляем
            log.exception("Сводка руководителю HR %s не отправлена", head.id)
    return ok


# --- расписание и догон -----------------------------------------------------

_due_lock = asyncio.Lock()  # задача по расписанию и задача догона не должны отправить одну неделю дважды


def due_week_end(now: datetime | None = None) -> date:
    """Воскресенье последней недели, сводка за которую уже должна была уйти (понедельник, 09:00 МСК)."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local = now.astimezone(_tz())
    monday = local.date() - timedelta(days=local.weekday())
    if local.weekday() == 0 and local.time() < SEND_AT:
        monday -= timedelta(days=7)
    return monday - timedelta(days=1)


async def send_due(session, now: datetime | None = None) -> bool:
    """Сводка за прошедшую неделю, если её ещё не отправили. True — отправили сейчас.

    Вызывают задача по расписанию (понедельник, 09:00) и задача догона: если в понедельник приложение
    не работало, сводка уйдёт позже на этой неделе. Не ушла (руководитель HR не подключён, бот выключен) —
    попробуем снова в следующий раз. Коммит — за вызывающим.
    """
    now = now or datetime.now(UTC)
    week_end = due_week_end(now)
    async with _due_lock:
        row = await session.get(Setting, SENT_KEY)
        if row is not None and isinstance(row.value, str) and row.value >= week_end.isoformat():
            return False
        if not await send(session, now, week_end=week_end):
            return False
        if row is None:
            session.add(Setting(key=SENT_KEY, value=week_end.isoformat()))
        else:
            row.value = week_end.isoformat()
        await session.flush()
    log.info("Сводка за неделю по %s отправлена", week_end.isoformat())
    return True
