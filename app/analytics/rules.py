"""Правила риска: прозрачные условия над фактами о новичке, без чёрного ящика.

Каждое правило — функция `Facts → list[Reason]`. Причина пишется человеческим языком:
«оценка упала с 9 до 5», «нет ответа HR 2 дня». Уровень новичка — высокий, если есть хоть одна
причина высокого уровня или три средних, иначе средний.

Правила (коды причин):
- red_flag — открытый красный сигнал (безопасность, травля, угрозы, здоровье). Высокий;
- intent — намерение уйти: сигнал «намерение уйти» (кнопка в итоговом опросе или текст), открытый
  или за последние 30 дней — высокий; ответ «Не уверен» на вопрос «Планируешь продолжить работу?» — средний;
  тексты причин — без рода: «Думает об уходе», «Молчит 9 дней», «Бот отключён»;
- yellow_flag — открытые жёлтые сигналы (нагрузка, понимание задач, наставник…). Средний;
- score_drop — средняя оценка опроса упала на 3 и больше пункта к прошлому опросу с оценками. Высокий;
- low_score — средняя оценка последнего опроса с оценками ниже 6. Средний;
- missed_surveys — не прошёл 2 опроса подряд. Средний;
- silent — молчит 7 дней и больше, а бот ждёт ответа на опрос. Средний;
- not_connected — не подключился к боту за день до выхода или позже. Средний, с 5-го дня — высокий;
- meeting_overdue — встреча с руководителем просрочена больше чем на 3 рабочих дня. Средний;
- ticket_waiting — обращение без ответа HR больше суток. Средний;
- bot_stopped — новичок отключил бота: сообщения не доходят. Высокий.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

HIGH, MEDIUM = "high", "medium"
LEVEL_LABELS = {HIGH: "Высокий", MEDIUM: "Средний"}

INTENT_TOPIC = "намерение уйти"
INTENT_RECENT = timedelta(days=30)
LOW_SCORE = 6.0
DROP = 3.0
MISSED_IN_ROW = 2
SILENT_DAYS = 7
NOT_CONNECTED_HIGH_DAY = 5
MEETING_OVERDUE_WORKDAYS = 3
TICKET_WAIT = timedelta(hours=24)
MEDIUM_FOR_HIGH = 3  # столько средних причин — уже высокий уровень

# Короткие подписи причин: сводка, фильтры, «3 новичка в зоне риска: падение оценок, намерение уйти»
REASON_LABELS = {
    "red_flag": "красные сигналы",
    "intent": "намерение уйти",
    "score_drop": "падение оценок",
    "low_score": "низкие оценки",
    "yellow_flag": "жёлтые сигналы",
    "missed_surveys": "пропуск опросов",
    "silent": "молчат",
    "not_connected": "не подключились",
    "meeting_overdue": "просрочены встречи",
    "ticket_waiting": "нет ответа HR",
    "bot_stopped": "отключили бота",
}


@dataclass
class Reason:
    code: str
    level: str
    text: str
    at: datetime | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "label": REASON_LABELS.get(self.code, self.code),
                "text": self.text, "at": self.at.isoformat() if self.at else None}


@dataclass
class FlagFact:
    level: str                  # red | yellow
    topic: str
    status: str                 # new | resolved
    source: str                 # survey | button | llm | message
    created_at: datetime


@dataclass
class SurveyScore:
    """Средняя оценка опроса (шкалы 1–10) у новичка."""
    code: str
    title: str
    at: datetime
    avg: float


@dataclass
class SurveyState:
    """Опрос в карточке новичка: done | pending | missed (как в Mini App)."""
    title: str
    status: str
    on: date | None = None      # день, когда опрос пришёл


@dataclass
class Meeting:
    title: str
    due: date
    overdue_workdays: int       # рабочих дней после даты встречи по «сегодня» новичка


@dataclass
class Facts:
    """Всё, что правила знают о новичке. Собирает app.analytics.risks, тесты — руками."""
    day: int                                  # день адаптации: 1 — день выхода, ≤ 0 — до выхода
    status: str = "active"                    # waiting | active | stopped | finished
    connected: bool = True
    flags: list[FlagFact] = field(default_factory=list)
    scores: list[SurveyScore] = field(default_factory=list)    # по времени
    surveys: list[SurveyState] = field(default_factory=list)   # по времени
    stay_unsure_at: datetime | None = None    # «Не уверен» на «Планируешь продолжить работу?»
    last_activity: datetime | None = None     # последний ответ, вопрос или сообщение новичка
    meetings: list[Meeting] = field(default_factory=list)      # непроведённые встречи
    tickets_waiting: list[datetime] = field(default_factory=list)  # открытые обращения без ответа HR


# --- слова --------------------------------------------------------------------

def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


DAYS = ("день", "дня", "дней")


def num(x: float) -> str:
    """9 → «9», 8.64 → «8,6»."""
    r = round(float(x), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}".replace(".", ",")


def ago(when: datetime | None, now: datetime) -> str:
    """«сегодня», «вчера», «3 дня назад»."""
    if when is None:
        return ""
    days = (now - when).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} {plural(days, DAYS)} назад"


def _topics(flags: list[FlagFact]) -> str:
    seen: list[str] = []
    for f in sorted(flags, key=lambda x: x.created_at, reverse=True):
        if f.topic not in seen:
            seen.append(f.topic)
    return ", ".join(seen)


# --- правила ------------------------------------------------------------------

def _open(f: FlagFact) -> bool:
    return f.status != "resolved"


def red_flags(f: Facts, now: datetime) -> list[Reason]:
    red = [x for x in f.flags if x.level == "red" and _open(x) and x.topic != INTENT_TOPIC]
    if not red:
        return []
    last = max(red, key=lambda x: x.created_at)
    if len(red) == 1:
        text = f"Красный сигнал: {last.topic}, {ago(last.created_at, now)}"
    else:
        text = f"{len(red)} красных {plural(len(red), ('сигнал', 'сигнала', 'сигналов'))}: {_topics(red)}"
    return [Reason("red_flag", HIGH, text, last.created_at)]


def intent(f: Facts, now: datetime) -> list[Reason]:
    hits = [x for x in f.flags if x.topic == INTENT_TOPIC and (_open(x) or now - x.created_at <= INTENT_RECENT)]
    if hits:
        last = max(hits, key=lambda x: x.created_at)
        how = "ответ в опросе" if last.source == "button" else "сообщение"
        return [Reason("intent", HIGH, f"Думает об уходе: {how} {ago(last.created_at, now)}", last.created_at)]
    if f.stay_unsure_at is not None:
        return [Reason("intent", MEDIUM, "Сомневается, что останется после испытательного", f.stay_unsure_at)]
    return []


def yellow_flags(f: Facts, now: datetime) -> list[Reason]:
    yellow = [x for x in f.flags if x.level == "yellow" and _open(x) and x.topic != INTENT_TOPIC]
    if not yellow:
        return []
    last = max(yellow, key=lambda x: x.created_at)
    if len(yellow) == 1:
        text = f"Жёлтый сигнал: {last.topic}"
    else:
        text = f"{len(yellow)} жёлтых {plural(len(yellow), ('сигнал', 'сигнала', 'сигналов'))}: {_topics(yellow)}"
    return [Reason("yellow_flag", MEDIUM, text, last.created_at)]


def scores(f: Facts, now: datetime) -> list[Reason]:
    if not f.scores:
        return []
    last = f.scores[-1]
    if len(f.scores) >= 2:
        prev = f.scores[-2]
        if prev.avg - last.avg >= DROP:
            return [Reason("score_drop", HIGH, f"Оценка упала с {num(prev.avg)} до {num(last.avg)}", last.at)]
    if last.avg < LOW_SCORE:
        return [Reason("low_score", MEDIUM, f"Оценка {num(last.avg)} из 10 в опросе «{last.title}»", last.at)]
    return []


def missed_surveys(f: Facts, now: datetime) -> list[Reason]:
    """Хвост из пропущенных опросов. Свежий опрос, на который ещё можно ответить, хвост не обрывает."""
    run: list[SurveyState] = []
    for s in reversed(f.surveys):
        if s.status == "pending" and not run:
            continue
        if s.status != "missed":
            break
        run.append(s)
    if len(run) < MISSED_IN_ROW:
        return []
    return [Reason("missed_surveys", MEDIUM,
                   f"{len(run)} {plural(len(run), ('опрос', 'опроса', 'опросов'))} подряд без ответа", None)]


def silent(f: Facts, now: datetime) -> list[Reason]:
    """Бот ждёт ответа на опрос, а новичок молчит неделю и дольше."""
    if f.status != "active" or not f.connected or f.day < 1 or f.last_activity is None:
        return []
    days = (now - f.last_activity).days
    if days < SILENT_DAYS:
        return []
    since = f.last_activity.date()
    waiting = [s for s in f.surveys if s.status in ("pending", "missed") and (s.on is None or s.on >= since)]
    if not waiting:
        return []
    return [Reason("silent", MEDIUM,
                   f"Молчит {days} {plural(days, DAYS)}, нет ответа на «{waiting[-1].title}»", f.last_activity)]


def not_connected(f: Facts, now: datetime) -> list[Reason]:
    if f.connected or f.status == "stopped" or f.day < 0:
        return []
    if f.day == 0:
        text = "Выход завтра, а в боте ещё нет"
    elif f.day == 1:
        text = "Выход сегодня, а в боте ещё нет"
    else:
        text = f"В боте ещё нет, хотя выход был {f.day - 1} {plural(f.day - 1, DAYS)} назад"
    return [Reason("not_connected", HIGH if f.day >= NOT_CONNECTED_HIGH_DAY else MEDIUM, text, None)]


def meetings(f: Facts, now: datetime) -> list[Reason]:
    late = [m for m in f.meetings if m.overdue_workdays > MEETING_OVERDUE_WORKDAYS]
    if not late:
        return []
    m = max(late, key=lambda x: x.overdue_workdays)
    n = m.overdue_workdays
    days = plural(n, ("рабочий день", "рабочих дня", "рабочих дней"))
    return [Reason("meeting_overdue", MEDIUM, f"{m.title}: просрочена на {n} {days}", None)]


def tickets(f: Facts, now: datetime) -> list[Reason]:
    late = [t for t in f.tickets_waiting if now - t > TICKET_WAIT]
    if not late:
        return []
    oldest = min(late)
    days = (now - oldest).days
    text = "Нет ответа HR больше суток" if days < 2 else f"Нет ответа HR {days} {plural(days, DAYS)}"
    if len(late) > 1:
        text += f", обращений: {len(late)}"
    return [Reason("ticket_waiting", MEDIUM, text, oldest)]


def bot_stopped(f: Facts, now: datetime) -> list[Reason]:
    if f.status != "stopped":
        return []
    return [Reason("bot_stopped", HIGH, "Бот отключён: сообщения не доходят", None)]


RULES = (red_flags, intent, scores, bot_stopped, not_connected, yellow_flags, missed_surveys, silent,
         meetings, tickets)


def evaluate(f: Facts, now: datetime) -> tuple[str | None, list[Reason]]:
    """(уровень или None — риска нет, причины по важности)."""
    reasons = [r for rule in RULES for r in rule(f, now)]
    if not reasons:
        return None, []
    reasons.sort(key=lambda r: r.level != HIGH)  # высокие первыми, внутри — порядок правил
    high = any(r.level == HIGH for r in reasons) or len(reasons) >= MEDIUM_FOR_HIGH
    return (HIGH if high else MEDIUM), reasons
