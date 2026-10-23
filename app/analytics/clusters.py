"""Кластеры тем из ответов опросов: о чём новички пишут чаще, динамика к прошлому периоду, цитаты.

Тема ответа — `SurveyAnswer.topic` (ИИ-разбор текста при согласии) или тема вопроса. Упоминание — ответ,
который говорит о трудности: свой текст (кроме позитивного) или не позитивный ответ кнопкой.
«Другое» и пустая тема — не кластер.

Динамика — к прошлым `weeks` неделям. Цитаты — 1–2 на тему, только из ответов новичков с согласием
`ai_analysis` (последнее решение), без текстов о здоровье. Имена, телефоны и номера маскируются
(`app.privacy.mask` + `app.privacy.names.known_names`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.analytics.rules import plural
from app.db.models import Consent, Employee, SurveyAnswer, SurveyQuestion
from app.privacy.health import is_health
from app.privacy.mask import mask
from app.reports.weekly import mood_of, topic_label

SKIP_TOPICS = {"", "другое", "other", "none"}
QUOTES = 2
QUOTE_LEN = 160
_LABELS = re.compile(r"\[(?:ФИО|ТЕЛЕФОН|EMAIL|НОМЕР)\]")


def is_mention(row: dict) -> bool:
    """Ответ говорит о трудности: свой текст (не позитивный) или не позитивная кнопка."""
    m = mood_of(row.get("mood"), row.get("score"))
    if (row.get("text") or "").strip():
        return m != "positive"
    return m in ("neutral", "negative")


def trend_text(count: int, prev: int, weeks: int = 4) -> str:
    """«в 2 раза больше, чем месяц назад», «на 3 меньше…», «новая тема»."""
    if not prev:
        return "новая тема" if count else ""
    than = "чем месяц назад" if weeks == 4 else f"чем за прошлые {weeks} {plural(weeks, ('неделю', 'недели', 'недель'))}"
    ratio = count / prev
    if ratio >= 1.5 and count - prev >= 2:
        if ratio < 1.95:
            return f"в 1,5 раза больше, {than}"
        n = round(ratio)
        return f"в {n} {plural(n, ('раз', 'раза', 'раз'))} больше, {than}"
    if count > prev:
        return f"на {count - prev} больше, {than}"
    if count == prev:
        return "столько же, сколько месяц назад" if weeks == 4 else f"столько же, {than}"
    return f"на {prev - count} меньше, {than}"


def trend(count: int, prev: int) -> str:
    """up | down | flat | new — для стрелки на экране."""
    if not prev:
        return "new" if count else "flat"
    if count >= prev * 1.5 and count - prev >= 2:
        return "up"
    if count * 1.5 <= prev and prev - count >= 2:
        return "down"
    return "flat"


def clip(text: str, limit: int = QUOTE_LEN) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:—–- ")
    return cut + "…"


def _quote_text(row: dict) -> str:
    """Дословная цитата ИИ-разбора, если она есть в ответе; иначе сам ответ."""
    ev = " ".join((row.get("evidence_quote") or "").split())
    text = row.get("text") or ""
    if ev and ev.lower() in " ".join(text.split()).lower():
        return ev
    return text


async def ai_consents(session, ids: list[int]) -> set[int]:
    """Новички, чьё последнее решение по ai_analysis — «да»."""
    if not ids:
        return set()
    rows = await session.execute(
        select(Consent.employee_id, Consent.given, Consent.given_at, Consent.id)
        .where(Consent.employee_id.in_(ids), Consent.purpose == "ai_analysis"))
    last: dict[int, tuple] = {}
    for eid, given, at, cid in rows.all():
        key = (at or datetime.min.replace(tzinfo=UTC), cid)
        if eid not in last or key > last[eid][0]:
            last[eid] = (key, given)
    return {eid for eid, (_, given) in last.items() if given}


async def load_rows(session, lo: datetime, hi: datetime, *, site: str | None = None) -> list[dict]:
    q = (select(SurveyAnswer.id, SurveyAnswer.employee_id, Employee.site_code,
                func.coalesce(SurveyAnswer.topic, SurveyQuestion.topic), SurveyAnswer.mood, SurveyAnswer.score,
                SurveyAnswer.text, SurveyAnswer.evidence_quote, SurveyAnswer.created_at,
                SurveyAnswer.is_synthetic, Employee.is_synthetic)
         .join(Employee, Employee.id == SurveyAnswer.employee_id)
         .join(SurveyQuestion, SurveyQuestion.id == SurveyAnswer.question_id)
         .where(SurveyAnswer.created_at >= lo, SurveyAnswer.created_at < hi))
    if site:
        q = q.where(Employee.site_code == site)
    return [{"id": r[0], "employee_id": r[1], "site": r[2], "topic": r[3], "mood": r[4], "score": r[5],
             "text": r[6], "evidence_quote": r[7], "created_at": r[8], "synthetic": bool(r[9] or r[10])}
            for r in (await session.execute(q)).all()]


def aggregate(rows: list[dict], lo: datetime, *, weeks: int = 4, consents: set[int] | frozenset = frozenset(),
              names: list[str] | None = None, quotes: int = QUOTES) -> list[dict]:
    """Кластеры из строк ответов за два периода: [lo − weeks, lo) — прошлый, [lo, …) — текущий."""
    groups: dict[str, dict] = {}
    for row in rows:
        topic = (row.get("topic") or "").strip()
        if topic.lower() in SKIP_TOPICS or not is_mention(row):
            continue
        key = topic.lower()
        g = groups.setdefault(key, {"topic": topic, "now": [], "prev": []})
        g["now" if row["created_at"] >= lo else "prev"].append(row)

    out = []
    for key, g in groups.items():
        cur, prev = g["now"], g["prev"]
        if not cur and not prev:
            continue
        sites: dict[str, dict] = defaultdict(lambda: {"count": 0, "prev": 0, "people": set()})
        for r in cur:
            s = sites[r["site"]]
            s["count"] += 1
            s["people"].add(r["employee_id"])
        for r in prev:
            sites[r["site"]]["prev"] += 1
        out.append({
            "topic": key,
            "label": topic_label(g["topic"]),
            "count": len(cur),
            "people": len({r["employee_id"] for r in cur}),
            "negative": sum(1 for r in cur if mood_of(r.get("mood"), r.get("score")) == "negative"),
            "prev": len(prev),
            "delta": len(cur) - len(prev),
            "trend": trend(len(cur), len(prev)),
            "trend_text": trend_text(len(cur), len(prev), weeks),
            "sites": sorted(({"code": c, "count": s["count"], "prev": s["prev"], "people": len(s["people"])}
                             for c, s in sites.items()), key=lambda s: (-s["count"], -s["prev"], s["code"])),
            "quotes": pick_quotes(cur, consents, names, quotes),
            "synthetic": any(r["synthetic"] for r in cur),
        })
    out.sort(key=lambda c: (-c["count"], -c["delta"], c["label"]))
    return [c for c in out if c["count"] or c["prev"]]


def pick_quotes(rows: list[dict], consents: set[int] | frozenset, names: list[str] | None,
                limit: int = QUOTES) -> list[dict]:
    """1–2 обезличенные цитаты от разных новичков, только с согласием на ИИ-разбор и не о здоровье."""
    cands = []
    for r in rows:
        if r["employee_id"] not in consents or not (r.get("text") or "").strip() or is_health(r["text"]):
            continue
        text = clip(mask(_quote_text(r), names))
        if not re.search(r"\w{3,}", _LABELS.sub("", text)):
            continue  # от цитаты остались только метки
        cands.append((r, text))
    # сначала негативные, среди них — те, что читаются целиком (без меток маскирования), потом свежие
    cands.sort(key=lambda c: (mood_of(c[0].get("mood"), c[0].get("score")) != "negative",
                              bool(_LABELS.search(c[1])), -c[0]["created_at"].timestamp()))
    out, seen = [], set()
    for r, text in cands:
        if r["employee_id"] in seen:
            continue
        seen.add(r["employee_id"])
        out.append({"text": text, "site": r["site"], "mood": mood_of(r.get("mood"), r.get("score")),
                    "at": r["created_at"].isoformat()})
        if len(out) >= limit:
            break
    return out


async def clusters(session, weeks: int = 4, now: datetime | None = None, *, site: str | None = None,
                   until: datetime | None = None, quotes: int = QUOTES) -> list[dict]:
    """Темы с числом упоминаний за `weeks` недель до `until` (по умолчанию — сейчас) и динамикой к прошлым `weeks`.

    [{topic, label, count, people, negative, prev, delta, trend, trend_text, sites: [{code, count, prev, people}],
      quotes: [{text, site, mood, at}], synthetic}] — по убыванию упоминаний.
    """
    from app.privacy.names import known_names

    hi = until or now or datetime.now(UTC)
    if hi.tzinfo is None:
        hi = hi.replace(tzinfo=UTC)
    lo = hi - timedelta(weeks=weeks)
    rows = await load_rows(session, lo - timedelta(weeks=weeks), hi, site=site)
    consents = await ai_consents(session, sorted({r["employee_id"] for r in rows if r["created_at"] >= lo}))
    names = await known_names(session) if consents else []
    return aggregate(rows, lo, weeks=weeks, consents=consents, names=names, quotes=quotes)
