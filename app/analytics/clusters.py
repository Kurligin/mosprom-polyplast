"""Кластеры тем из ответов опросов: о чём новички пишут чаще, динамика к прошлому периоду, цитаты.

Тема ответа — `SurveyAnswer.topic` (ИИ-разбор текста при согласии) или тема вопроса. Упоминание — ответ,
который говорит о трудности: свой текст (кроме позитивного) или не позитивный ответ кнопкой.
«Другое» и пустая тема — не кластер.

Динамика — к прошлым `weeks` неделям. Цитаты — 1–2 на тему, только из ответов новичков с согласием
`ai_analysis` (последнее решение), без текстов о здоровье. Имена, телефоны и номера маскируются
(`app.privacy.mask` + `app.privacy.names.known_names`).

Приватность (автора темы не вычислить по экрану, где рядом список рисков с именами):
- за окно ответили меньше `THRESHOLD` (5) новичков (в срезе площадки или всего) — кластеров нет, как у настроения;
- тема одного человека — не кластер (`MIN_PEOPLE`);
- разбивка по площадкам — только площадки, где о теме пишут от 5 человек, остальные — «Другие площадки»;
- цитаты — только у темы, о которой пишут от 5 человек, без площадки; не из ответов, по которым поднят сигнал
  (этот текст уже в именном обращении), и не там, где после маскирования осталось похожее на имя слово
  (фамилия с заглавной не в начале предложения, отчество): лучше потерять цитату, чем показать фамилию;
- нет цитат — причина без подробностей о людях: `quotes_reason` = few | no_text | hidden.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.analytics.rules import plural
from app.db.models import Consent, Employee, Flag, Site, SurveyAnswer, SurveyQuestion
from app.privacy.health import is_health
from app.privacy.mask import mask
from app.reports.weekly import mood_of, topic_label

SKIP_TOPICS = {"", "другое", "other", "none"}
QUOTES = 2
QUOTE_LEN = 160
THRESHOLD = 5          # как в сводке: срез, разбивка по площадке и цитаты — от 5 человек
MIN_PEOPLE = 2         # тема одного человека — его история, а не тема
OTHER_SITES = "other"  # площадки, где о теме пишут меньше THRESHOLD человек, одной строкой
OTHER_SITES_NAME = "Другие площадки"
_LABELS = re.compile(r"\[(?:ФИО|ТЕЛЕФОН|EMAIL|НОМЕР)\]")

# --- похоже на имя после маскирования ---
_WORD_RE = re.compile(r"(?<!\w)[А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)*")
_PATRONYMIC_RE = re.compile(r"(ович|евич|овна|евна|ична|ыч|ич)$")   # у слова с заглавной: Петрович, Ильич, Палыч
_PATRONYMIC_ANY_RE = re.compile(r"(ович|евич|ыч)$")                   # и со строчной: «петрович», «палыч»
_NOT_SENTENCE_END = ",;:)]»\"'%"
# С заглавной, но не имена: Корсик и «МАКС» (слово целиком), города и компания (по основе — в любом падеже)
_ALLOWED_WORDS = {"Я", "Корсик", "Корсика", "Корсику", "Корсиком", "Корсике", "Макс", "Максе"}
_ALLOWED_STEMS = ("Москв", "Росси", "Полипласт", "Групп")


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


def _sentence_start(text: str, pos: int) -> bool:
    head = text[:pos].rstrip(" \t«\"'([—–-")
    if not head or head[-1] in ".!?…\n":
        return True
    last = head[-1]
    return not last.isalnum() and last not in _NOT_SENTENCE_END  # эмодзи в конце фразы — тоже конец предложения


def has_name(text: str, allowed=()) -> bool:
    """После маскирования осталось похожее на имя: слово с заглавной не в начале предложения (кроме аббревиатур,
    названий площадок и белого списка) или отчество («Петрович», «палыч»). Тогда цитату не показываем.

    allowed — слова с заглавной, которые не имена (названия и коды площадок), в любом падеже.
    """
    words = set(_ALLOWED_WORDS)
    stems = list(_ALLOWED_STEMS)
    for a in allowed:
        a = (a or "").strip()
        if a:
            words.add(a)
            if len(a) >= 6:
                stems.append(a[:-2])  # «Лужники» → «Лужниках»
    for m in _WORD_RE.finditer(text or ""):
        w = m.group()
        if _PATRONYMIC_ANY_RE.search(w.lower()):
            return True
        if not w[0].isupper() or (len(w) > 1 and w.isupper()) or w in words or w.startswith(tuple(stems)):
            continue
        if _PATRONYMIC_RE.search(w.lower()) or not _sentence_start(text, m.start()):
            return True
    return False


def _sites(cur: list[dict], prev: list[dict], site_min: int) -> list[dict]:
    """Разбивка темы по площадкам: площадки, где о теме пишут от site_min человек, остальные — одной строкой."""
    sites: dict[str, dict] = defaultdict(lambda: {"count": 0, "prev": 0, "people": set()})
    for r in cur:
        s = sites[r["site"]]
        s["count"] += 1
        s["people"].add(r["employee_id"])
    for r in prev:
        sites[r["site"]]["prev"] += 1
    shown, rest = [], {"count": 0, "prev": 0, "people": set()}
    for code, s in sites.items():
        if len(s["people"]) >= site_min:
            shown.append({"code": code, "count": s["count"], "prev": s["prev"], "people": len(s["people"])})
        else:
            rest["count"] += s["count"]
            rest["prev"] += s["prev"]
            rest["people"] |= s["people"]
    shown.sort(key=lambda s: (-s["count"], -s["prev"], s["code"]))
    if shown and rest["count"]:
        shown.append({"code": OTHER_SITES, "name": OTHER_SITES_NAME, "count": rest["count"], "prev": rest["prev"],
                      "people": len(rest["people"])})
    return shown


def aggregate(rows: list[dict], lo: datetime, *, weeks: int = 4, consents: set[int] | frozenset = frozenset(),
              names: list[str] | None = None, quotes: int = QUOTES, flagged: set[int] | frozenset = frozenset(),
              allowed=(), min_people: int = MIN_PEOPLE, site_min: int = THRESHOLD,
              quote_min: int = THRESHOLD) -> list[dict]:
    """Кластеры из строк ответов за два периода: [lo − weeks, lo) — прошлый, [lo, …) — текущий.

    flagged — id ответов, по которым поднят сигнал (в цитаты не берём), allowed — слова с заглавной, которые
    не имена (`has_name`). Пороги приватности — в докстринге модуля.
    """
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
        people = len({r["employee_id"] for r in cur})
        if people < max(min_people, 1):
            continue  # тема одного человека (или только прошлый период) — не кластер
        picked = []
        if quotes and people >= quote_min:
            picked = pick_quotes([r for r in cur if r.get("id") not in flagged], consents, names, quotes, allowed)
        reason = None
        if not picked:
            reason = ("few" if people < quote_min else
                      "no_text" if not any((r.get("text") or "").strip() for r in cur) else "hidden")
        out.append({
            "topic": key,
            "label": topic_label(g["topic"]),
            "count": len(cur),
            "people": people,
            "negative": sum(1 for r in cur if mood_of(r.get("mood"), r.get("score")) == "negative"),
            "prev": len(prev),
            "delta": len(cur) - len(prev),
            "trend": trend(len(cur), len(prev)),
            "trend_text": trend_text(len(cur), len(prev), weeks),
            "sites": _sites(cur, prev, site_min),
            "quotes": picked,
            "quotes_reason": reason,
            "synthetic": any(r["synthetic"] for r in cur),
        })
    out.sort(key=lambda c: (-c["count"], -c["delta"], c["label"]))
    return out


def pick_quotes(rows: list[dict], consents: set[int] | frozenset, names: list[str] | None,
                limit: int = QUOTES, allowed=()) -> list[dict]:
    """1–2 обезличенные цитаты от разных новичков, только с согласием на ИИ-разбор и не о здоровье.

    Без площадки: по площадке и цитате автора легко найти в списке рисков. После маскирования осталось
    похожее на имя слово (`has_name`) — цитату не берём.
    """
    cands = []
    for r in rows:
        if r["employee_id"] not in consents or not (r.get("text") or "").strip() or is_health(r["text"]):
            continue
        text = clip(mask(_quote_text(r), names))
        if not re.search(r"\w{3,}", _LABELS.sub("", text)):
            continue  # от цитаты остались только метки
        if has_name(text, allowed):
            continue  # фамилия или отчество, которых нет в базе: маскирование их не знает
        cands.append((r, text))
    # сначала негативные, среди них — те, что читаются целиком (без меток маскирования), потом свежие
    cands.sort(key=lambda c: (mood_of(c[0].get("mood"), c[0].get("score")) != "negative",
                              bool(_LABELS.search(c[1])), -c[0]["created_at"].timestamp()))
    out, seen = [], set()
    for r, text in cands:
        if r["employee_id"] in seen:
            continue
        seen.add(r["employee_id"])
        out.append({"text": text, "mood": mood_of(r.get("mood"), r.get("score")), "at": r["created_at"].isoformat()})
        if len(out) >= limit:
            break
    return out


async def flagged_answers(session, ids: list[int]) -> set[int]:
    """Ответы, по которым поднят сигнал: их текст уже в именном обращении и в причине риска."""
    if not ids:
        return set()
    return {a for a in (await session.scalars(select(Flag.answer_id).where(Flag.answer_id.in_(ids)))).all() if a}


async def site_words(session) -> set[str]:
    """Слова из названий и кодов площадок: «Лужники», «GTP» — не имена."""
    out: set[str] = set()
    for code, name in (await session.execute(select(Site.code, Site.name))).all():
        for w in re.findall(r"[\wЁё]+", f"{code} {name or ''}"):
            out.add(w)
    return out


async def cluster_view(session, weeks: int = 4, now: datetime | None = None, *, site: str | None = None,
                       until: datetime | None = None, quotes: int = QUOTES, site_min: int = THRESHOLD) -> dict:
    """{clusters, low_data, respondents}: low_data — за окно ответили меньше THRESHOLD новичков, тем не показываем."""
    from app.privacy.names import known_names

    hi = until or now or datetime.now(UTC)
    if hi.tzinfo is None:
        hi = hi.replace(tzinfo=UTC)
    lo = hi - timedelta(weeks=weeks)
    rows = await load_rows(session, lo - timedelta(weeks=weeks), hi, site=site)
    current = [r for r in rows if r["created_at"] >= lo]
    respondents = len({r["employee_id"] for r in current})
    if respondents < THRESHOLD:
        return {"clusters": [], "low_data": True, "respondents": respondents}
    consents = await ai_consents(session, sorted({r["employee_id"] for r in current})) if quotes else set()
    names = await known_names(session) if consents else []
    flagged = await flagged_answers(session, [r["id"] for r in current if (r.get("text") or "").strip()]) \
        if consents else set()
    allowed = await site_words(session) if consents else set()
    found = aggregate(rows, lo, weeks=weeks, consents=consents, names=names, quotes=quotes, flagged=flagged,
                      allowed=allowed, site_min=site_min)
    return {"clusters": found, "low_data": False, "respondents": respondents}


async def clusters(session, weeks: int = 4, now: datetime | None = None, *, site: str | None = None,
                   until: datetime | None = None, quotes: int = QUOTES, site_min: int = THRESHOLD) -> list[dict]:
    """Темы с числом упоминаний за `weeks` недель до `until` (по умолчанию — сейчас) и динамикой к прошлым `weeks`.

    [{topic, label, count, people, negative, prev, delta, trend, trend_text,
      sites: [{code, count, prev, people, name?}], quotes: [{text, mood, at}], quotes_reason, synthetic}] —
    по убыванию упоминаний. Пороги приватности — в докстринге модуля; site_min — с какого числа людей площадка
    видна в разбивке отдельно (сводка проверяет срез сама и передаёт 2).
    """
    return (await cluster_view(session, weeks, now, site=site, until=until, quotes=quotes,
                               site_min=site_min))["clusters"]
