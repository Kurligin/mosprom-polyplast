"""Истории риска для экрана «Аналитика» и блока «Зоны риска» в сводке (все данные синтетические).

Запуск: python -m seed.risks_seed. Вызывается и из make seed: в конце seed.seed и seed.surveys_seed.

На общей синтетике видны красные и жёлтые сигналы и низкие оценки. Остальные правила риска
(app/analytics/rules.py) показывают пять новичков с готовой историей:
- «Образцов Демьян», GTP: в чеке 2-й недели оценки около 9, в итоговом опросе около 5 и «Не уверен» → падение оценок;
- «Шаблонова Эскиза», GrPP: ответила только в первый день, пропустила опрос 3-го дня и чек 2-й недели → молчит;
- «Макетова Примерна», GTP: вышла 3 дня назад, к боту не подключилась;
- «Пробникова Синтия», Лужники: подумывает уйти, встреча 30-го дня просрочена, вопрос без ответа HR 2 дня;
- «Моделин Шаблон», GrPP: трудные первые дни, отключил бота.

Новичок истории помечен `dialog_data["story"]`: общие сиды (ответы, вопросы, встречи) его не трогают.
Идемпотентно: каждая часть истории (новичок, согласия, ответы и сигналы, обращение, встречи) создаётся,
только если её ещё нет; даты — от дня первого запуска.

Ещё здесь согласия синтетических новичков: на сопровождение — у всех подключившихся, на ИИ-разбор —
у четырёх из пяти. Без согласия на ИИ-разбор цитат в кластерах тем нет.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import settings
from app.db.models import (
    Consent,
    Employee,
    Flag,
    QuestionLog,
    Staff,
    SurveyAnswer,
    Ticket,
    new_code,
)
from app.scenario.schedule import add_months
from app.signals import rules
from app.signals.service import TICKET_CATEGORY
from app.surveys import config as C
from app.surveys import store

STORY = "story"  # dialog_data: код истории
SYNTH = " [синтетика]"


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.tz_default)


@dataclass
class Ans:
    survey: str
    key: str
    value: str | None = None
    score: int | None = None
    text: str | None = None
    topic: str | None = None     # тема текста, как после ИИ-разбора
    mood: str | None = None      # None — из варианта ответа или оценки
    today: bool = False          # ответил сегодня (а не в день опроса по сценарию)


def scales(survey: str, keys: tuple[str, ...], scores: tuple[int, ...], **kw) -> list[Ans]:
    return [Ans(survey, k, str(s), s, **kw) for k, s in zip(keys, scores, strict=True)]


W2_SCALES = ("w2_manager", "w2_feedback", "w2_colleagues", "w2_workplace", "w2_access", "w2_hr", "w2_hr_hiring")
FIN_SCALES = ("fin_load", "fin_expectations", "fin_manager", "fin_feedback", "fin_colleagues", "fin_depts", "fin_hr",
              "fin_goals")


@dataclass
class Story:
    code: str
    last: str
    first: str
    site: str
    work_type: str
    position: str
    start_ago: int                       # вышел столько дней назад
    status: str = "active"
    probation_left: int | None = None    # испытательный закончится через N дней (иначе — 3 месяца)
    ai: bool = True                      # согласие на ИИ-разбор
    answers: list[Ans] = field(default_factory=list)
    missed: tuple[str, ...] = ()         # опросы пришли, но не пройдены (последний — ещё открыт)
    question: dict | None = None         # вопрос без ответа HR: text, topic, days_ago
    open_meeting: int | None = None      # эта встреча с руководителем остаётся непроведённой

    @property
    def full_name(self) -> str:
        return f"{self.last} {self.first}{SYNTH}"


STORIES = [
    Story(
        "score_drop", "Образцов", "Демьян", "GTP", "production", "Оператор экструдера", 0, probation_left=2,
        answers=[
            Ans("day1", "d1_access_gtp", "Да, все есть"),
            Ans("day3", "d3_docs", "Все отлично!"), Ans("day3", "d3_colleagues", "Отлично!"),
            Ans("day3", "d3_tasks", "Да, все понятно!"),
            Ans("week2", "w2_tasks", "Да"), Ans("week2", "w2_info", "Да"),
            *scales("week2", W2_SCALES, (9, 9, 10, 9, 9, 8, 9)),
            Ans("plan", "m1_plan", "Все хорошо! Продвигаюсь по плану"),
            Ans("month2", "m2_mood", "С безразличным: поработал и ушел"), Ans("month2", "m2_helpers", "Коллеги"),
            Ans("month2", "m2_speedup", "Более четкие задачи"),
            Ans("final", "fin_hardest", "Нечеткие задачи", today=True),
            Ans("final", "fin_stay", "Не уверен", today=True, topic="обучение", mood="negative",
                text="Обещали обучение на экструдере, а три месяца я на подхвате. Не понимаю, куда расту."),
            *scales("final", FIN_SCALES, (5, 4, 6, 5, 6, 5, 5, 4), today=True),
            Ans("final", "fin_info", "Нет", today=True, topic="понимание задач", mood="negative",
                text="Задачи ставят устно и каждый раз разные, записать некуда."),
        ],
    ),
    Story(
        "silent", "Шаблонова", "Эскиза", "GrPP", "office", "Специалист по закупкам", 20,
        answers=[Ans("day1", "d1_access", "Да, все есть")],
        missed=("day3", "week2"),
    ),
    Story("not_connected", "Макетова", "Примерна", "GTP", "production", "Укладчик-упаковщик", 3,
          status="waiting", ai=False),
    Story(
        "intent", "Пробникова", "Синтия", "Luzhniki", "office", "Бухгалтер", 38,
        answers=[
            Ans("day1", "d1_access", "Нет", topic="доступы",
                text="Учётку в 1С обещали завести к обеду, пока работаю через коллегу."),
            Ans("day3", "d3_docs", "Остались вопросы"), Ans("day3", "d3_colleagues", "Пока присматриваюсь"),
            Ans("day3", "d3_tasks", "Жду разъяснений от руководителя"),
            Ans("week2", "w2_tasks", "Да"),
            Ans("week2", "w2_info", "Нет", topic="доступы",
                text="Доступа к 1С так и нет, третью неделю проводки делаю через коллегу."),
            *scales("week2", W2_SCALES, (6, 5, 8, 5, 3, 7, 8)),
            Ans("plan", "m1_plan", "Я отстаю от графика.", topic="руководитель", mood="negative",
                text="С руководителем так и не встретились, план никто не обсуждает. "
                     "Если так и дальше, подумываю уйти."),
        ],
        question={"text": "Когда дадут доступ к 1С? Третью неделю работаю через коллегу", "topic": "доступы",
                  "days_ago": 2},
        open_meeting=30,
    ),
    Story(
        "stopped", "Моделин", "Шаблон", "GrPP", "production", "Оператор линии", 10, status="stopped",
        answers=[
            Ans("day1", "d1_access", "Нет"),
            Ans("day3", "d3_docs", "Все отлично!"), Ans("day3", "d3_colleagues", "Никак, все заняты"),
            Ans("day3", "d3_tasks", "Пока ничего не понял", topic="понимание задач",
                text="Каждый день ставят на новую линию, никто не объясняет, что делать."),
        ],
    ),
]


# --- время ---------------------------------------------------------------------

def survey_time(code: str, emp) -> datetime:
    from seed.surveys_seed import survey_at

    return survey_at(code, emp)


def answer_time(a: Ans, emp, now: datetime, n: int) -> datetime:
    """Через 10–30 минут после опроса; «сегодня» — пару часов назад. Не позже now."""
    if a.today:
        at = now - timedelta(hours=2) + timedelta(minutes=n)
    else:
        at = survey_time(a.survey, emp) + timedelta(minutes=10 + 2 * n)
    return min(at, now - timedelta(minutes=1))


# --- части истории -----------------------------------------------------------------

def _gates(st: Story) -> dict:
    """Отмашки рекрутера, которые к этому дню уже были: представили коллегам, план готов (с 2-го месяца)."""
    days = 88 if st.probation_left is not None else st.start_ago
    if st.status == "waiting" or days < 3:
        return {}
    return {"recruiter_go": True, "plan_ready": days >= 30}


async def _employee(s, st: Story, today: date, manager_id: int | None, recruiter_id: int | None) -> Employee:
    emp = await s.scalar(select(Employee).where(Employee.full_name == st.full_name, Employee.is_synthetic))
    if emp is not None:
        data = dict(emp.dialog_data or {})
        data[STORY] = st.code
        data.setdefault("gates", _gates(st))
        if data != (emp.dialog_data or {}):
            emp.dialog_data = data
        return emp
    if st.probation_left is not None:
        end = today + timedelta(days=st.probation_left)
        start = add_months(end, -3) + timedelta(days=1)
    else:
        start = today - timedelta(days=st.start_ago)
        end = add_months(start, 3)
    emp = Employee(
        full_name=st.full_name, first_name=st.first, site_code=st.site, work_type=st.work_type,
        start_date=start, probation_end=end, manager_id=manager_id, recruiter_id=recruiter_id,
        invite_code=new_code(), status=st.status, address_form="ty", is_synthetic=True,
        dialog_data={STORY: st.code, "position": st.position, "gates": _gates(st)},
    )
    s.add(emp)
    await s.flush()
    return emp


async def ensure_consents(s, emps: list[Employee], ai: dict[int, bool] | None = None) -> int:
    """Согласия синтетических новичков, которые подключились к боту. → сколько новичков получили согласия."""
    ai = ai or {}
    have = set(await s.scalars(select(Consent.employee_id).where(Consent.employee_id.in_([e.id for e in emps]))))
    n = 0
    for e in emps:
        if e.id in have or not e.is_synthetic or e.status == "waiting":
            continue
        at = datetime.combine(e.start_date - timedelta(days=2), time(10, 0), tzinfo=_tz())
        s.add(Consent(employee_id=e.id, purpose="adaptation", given=True, given_at=at, text_version="v1"))
        s.add(Consent(employee_id=e.id, purpose="ai_analysis", given=ai.get(e.id, e.id % 5 != 0),
                      given_at=at + timedelta(minutes=1), text_version="v1"))
        n += 1
    await s.flush()
    return n


async def _answers(s, st: Story, emp: Employee, ids: dict, surveys: dict, now: datetime) -> tuple[int, int]:
    """Ответы, сигналы по кнопкам и тексту, обращения по красным. → (ответов, сигналов)."""
    if not st.answers or await s.scalar(select(func.count()).select_from(SurveyAnswer)
                                        .where(SurveyAnswer.employee_id == emp.id)):
        return 0, 0
    flags = 0
    for n, a in enumerate(st.answers):
        sv = surveys[a.survey]
        q = sv.question(a.key)
        opt = next((o for o in q.options if o.label == a.value), None) if q is not None else None
        mood = a.mood or (opt.mood if opt else None)
        if mood is None and a.score is not None:
            mood = "positive" if a.score >= 8 else "neutral" if a.score >= 5 else "negative"
        at = answer_time(a, emp, now, n)
        ans = SurveyAnswer(employee_id=emp.id, question_id=ids[a.survey][a.key], value=a.value, score=a.score,
                           text=a.text, topic=a.topic or q.topic, mood=mood, is_synthetic=True, created_at=at,
                           evidence_quote=a.text.split(".")[0].strip() if a.text else None)
        s.add(ans)
        await s.flush()
        hits = []
        if opt is not None and opt.flag:
            quote = f"«{opt.label}». Опрос «{sv.title}», вопрос «{q.text}»."
            hits.append((opt.flag["level"], opt.flag["topic"], quote, "button"))
        for h in rules.detect(a.text or ""):
            hits.append((h.level, h.topic, a.text, "survey"))
        for level, topic, quote, source in hits:
            flag = Flag(employee_id=emp.id, level=level, topic=topic, quote=quote[:1000], source=source,
                        answer_id=ans.id, status="new", created_at=at)
            if level == "red":
                ticket = Ticket(employee_id=emp.id, source="flag", category=TICKET_CATEGORY.get(topic, topic),
                                text=quote[:1000], status="new", assignee_id=emp.recruiter_id, created_at=at)
                s.add(ticket)
                await s.flush()
                flag.ticket_id = ticket.id
            s.add(flag)
            flags += 1
    await s.flush()
    return len(st.answers), flags


def _missed(st: Story, emp: Employee) -> None:
    """Опросы пришли, но новичок их не прошёл: история опросов как у движка (последний — ещё открыт)."""
    if not st.missed or (emp.dialog_data or {}).get("surveys"):
        return
    data = dict(emp.dialog_data or {})
    history = {}
    times = [survey_time(code, emp) for code in st.missed]
    for i, (code, started) in enumerate(zip(st.missed, times, strict=True)):
        history[code] = {"started_at": started.isoformat()}
        if i + 1 < len(times):
            history[code]["closed_at"] = times[i + 1].isoformat()  # закрыт следующим опросом
    last = st.missed[-1]
    data["surveys"] = history
    data["survey"] = {"code": last, "q": 1, "mid": None, "started_at": history[last]["started_at"],
                      "reminded": 2, "done": False, "sel": [], "answer_id": None, "wait": None}
    emp.dialog_data = data


async def _question(s, st: Story, emp: Employee, now: datetime) -> bool:
    """Вопрос боту, которого нет в базе: обращение HR без ответа."""
    if not st.question:
        return False
    if await s.scalar(select(Ticket.id).where(Ticket.employee_id == emp.id, Ticket.source == "unknown").limit(1)):
        return False
    at = now - timedelta(days=st.question["days_ago"], hours=1)
    ticket = Ticket(employee_id=emp.id, source="unknown", category="unknown", text=st.question["text"],
                    status="new", assignee_id=emp.recruiter_id, created_at=at)
    s.add(ticket)
    await s.flush()
    s.add(QuestionLog(employee_id=emp.id, question=st.question["text"], route="unknown",
                      topic=st.question["topic"], ticket_id=ticket.id, created_at=at))
    await s.flush()
    return True


async def _meetings(s, st: Story, emp: Employee, today: date) -> None:
    """Прошедшие встречи проведены, кроме open_meeting. Отметки руководителя и отправленные push не трогаем."""
    if emp.manager_id is None or emp.status != "active":
        return
    from app.manager.service import ensure_tasks

    tz = _tz()
    for t in await ensure_tasks(s, emp):
        if t.status != "pending" or t.mid or t.due_date is None or t.due_date >= today:
            continue
        if st.open_meeting is not None and t.kind == "meeting" and t.day == st.open_meeting:
            continue
        t.status = "done"
        t.done_at = datetime.combine(t.due_date, time(15, 0), tzinfo=tz).astimezone(UTC)
    await s.flush()


# --- запуск -----------------------------------------------------------------------

async def ensure_stories(s, now: datetime | None = None) -> dict:
    """Истории риска и согласия синтетических новичков. Коммит — за вызывающим."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_tz()).date()
    surveys = C.load()
    ids = await store.sync(s)
    manager = await s.scalar(select(Staff).where(Staff.role == "manager").order_by(Staff.id).limit(1))
    recruiter = await s.scalar(select(Staff).where(Staff.role == "recruiter").order_by(Staff.id).limit(1))
    stats = {"stories": 0, "answers": 0, "flags": 0, "tickets": 0, "consents": 0}
    story_emps: list[tuple[Story, Employee]] = []
    for st in STORIES:
        emp = await _employee(s, st, today, manager.id if manager else None, recruiter.id if recruiter else None)
        story_emps.append((st, emp))
    await s.flush()

    synth = list((await s.scalars(select(Employee).where(Employee.is_synthetic).order_by(Employee.id))).all())
    stats["consents"] = await ensure_consents(s, synth, {e.id: st.ai for st, e in story_emps})
    for st, emp in story_emps:
        n_ans, n_flags = await _answers(s, st, emp, ids, surveys, now)
        _missed(st, emp)
        stats["answers"] += n_ans
        stats["flags"] += n_flags
        stats["tickets"] += await _question(s, st, emp, now)
        await _meetings(s, st, emp, today)
        stats["stories"] += 1
    await s.flush()
    return stats


def is_story(emp) -> bool:
    return bool((emp.dialog_data or {}).get(STORY))


async def main() -> None:
    from app.db.session import async_session, engine

    async with async_session() as s:
        if not await s.scalar(select(Employee.id).where(Employee.is_synthetic).limit(1)):
            print("Нет синтетических новичков: сначала python -m seed.seed")
            return
        stats = await ensure_stories(s)
        await s.commit()
    print(f"Истории риска: {stats['stories']}, новых ответов {stats['answers']}, сигналов {stats['flags']}, "
          f"обращений {stats['tickets']}, согласий у {stats['consents']} новичков [синтетика]")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
