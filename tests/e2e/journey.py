"""Путь сотрудника: оператор производства на площадке GTP, от приглашения до конца испытательного срока.

Сквозной прогон по настоящему коду бота: обработчики «МАКС» (`app.bot.runner.process_update`), сценарий
из xlsx, опросы из config/surveys.yaml, сигналы, обращения, задачи руководителя. API «МАКС» — фейк
(tests/e2e/conftest.py), поиск по базе знаний — фейк с пунктом ПВТР про обед, LLM и эмбеддинги выключены.

Время двигаем как в демо: машина времени (`engine.time_travel`) на ключевые дни (-2, 1, 3, 14, 60, end),
а между ними «живое время»: сдвиг часов новичка на каждый день, когда по плану что-то происходит.
После каждого шага вызываем задачи планировщика: scenario_tick, manager_tasks, survey_reminders.

Прогон пишет маршрут для людей: eval/journey/gtp-operator.md. Проверки — tests/e2e/test_e2e_journey.py.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import re
import traceback
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot import runner
from app.bot.handlers import BotCtx
from app.bot.sender import Sender
from app.config import settings
from app.db.models import (
    Consent,
    Delivery,
    Employee,
    Flag,
    KbChunk,
    KbDocument,
    ManagerTask,
    Message,
    QuestionLog,
    ScenarioStep,
    Site,
    Staff,
    Survey,
    SurveyAnswer,
    SurveyQuestion,
    Ticket,
)
from app.kb.search import Hit
from app.scenario import engine, store
from app.scenario.schedule import plan_for

ROOT = Path(__file__).resolve().parents[2]
XLSX = ROOT / "data" / "raw" / "korsik_scenario.xlsx"
MD_PATH = ROOT / "eval" / "journey" / "gtp-operator.md"
PVTR = ROOT / "docs" / "pvtr.md"
MSK = ZoneInfo("Europe/Moscow")

# --- персона и команда (синтетика) ------------------------------------------------------

START = date(2026, 11, 9)            # понедельник
PROBATION_END = date(2027, 2, 8)     # испытательный срок 3 месяца
INVITE = "INV-gtp-smirnov"


@dataclass(frozen=True)
class Person:
    key: str
    label: str
    full_name: str
    role: str | None
    user_id: int | None
    chat_id: int | None
    bind: str | None = None
    icon: str = "🧑‍💼"


NEWCOMER = Person("newcomer", "Иван", "Смирнов Иван (синтетика)", None, 100, 500, icon="👤")
HR = Person("hr", "HR Ольга", "Петрова Ольга (синтетика)", "hr", 201, 601, "STF-hr-petrova")
RECRUITER = Person("recruiter", "Рекрутер Мария", "Кузнецова Мария (синтетика)", "recruiter", 202, 602,
                   "STF-rec-kuznetsova")
MANAGER = Person("manager", "Руководитель Сергей", "Волков Сергей (синтетика)", "manager", 203, 603,
                 "STF-mgr-volkov", icon="👔")
HEAD = Person("head", "Глава подбора Елена", "Соколова Елена (синтетика)", "head_recruitment", 204, 604,
              "STF-head-sokolova")
HR_GROUP = Person("hr_group", "HR-группа", "HR-группа", None, None, 900, icon="👥")
CLOCK = Person("clock", "Время", "", None, None, None, icon="⏩")
# Проверки вне маршрута (в md не попадают): ещё два новичка той же площадки
PETR = Person("petr", "Пётр", "Орлов Пётр (синтетика)", None, 110, 510, icon="👤")
ANNA = Person("anna", "Анна", "Белова Анна (синтетика)", None, 120, 520, icon="👤")
STAFF = (HR, RECRUITER, MANAGER, HEAD)
PEOPLE = (NEWCOMER, HR, RECRUITER, MANAGER, HEAD, HR_GROUP, CLOCK, PETR, ANNA)
BY_CHAT = {p.chat_id: p for p in PEOPLE if p.chat_id is not None}
BY_KEY = {p.key: p for p in PEOPLE}

# --- что делают люди --------------------------------------------------------------------

QUESTION = "Во сколько у нас перерыв на обед?"
INTRO_TEXT = ("Я Иван, пять лет работал оператором экструдера на заводе упаковки. "
              "Умею настраивать линию и читать техкарты, люблю рыбалку.")
PHOTO = [{"type": "image", "payload": {"url": "https://example.invalid/ivan-smirnov.jpg"}}]
RED_TEXT = "Мастер угрожает, что уволит меня, если пожалуюсь на сломанный защитный кожух станка"
HR_REPLY = ("Иван, спасибо, что написал. Так быть не должно. Сегодня до 17:00 позвоню, "
            "разберёмся с мастером, а кожух уже передали в ремонт.")
FOLLOWUP = "Спасибо! После смены подойду в отдел кадров."
EDGE_RED = "Бригадир угрожает мне, что побьёт после смены, если расскажу про сломанный кожух"

# Кнопки в шагах сценария, которые новичок нажимает, если видит
STEP_PRESSES = ("Да, но немного волнуюсь", "Покажи карту", "Да!", "Пока, Корсик! 💙")

# Прохождение опросов: ("press", подпись кнопки) или ("say", текст)
SURVEY_SCRIPTS: dict[str, list[tuple[str, str]]] = {
    "day1": [("press", "Нет"), ("press", "Напомнить руководителю про доступы")],
    "day3": [("press", "Все отлично!"), ("press", "Пока присматриваюсь"), ("press", "Пока ничего не понял"),
             ("press", "Готово")],
    "week2": [("press", "Да"), ("press", "Нет"),
              ("say", "Не хватает инструкции по переналадке экструдера, спрашиваю у мастера каждый раз"),
              ("press", "9"), ("press", "8"), ("press", "9"), ("press", "8"), ("press", "10"), ("press", "9"),
              ("press", "10")],
    "plan": [("press", "Все ок! Немного отстаю от графика")],
    "month2": [("press", "С отличным! Мне все нравится!"), ("press", "Руководитель"), ("press", "Коллеги"),
               ("press", "Готово"), ("press", "Свой вариант"), ("say", "Наставник на смене в первую неделю")],
    "final": [("press", "Со всем справился"), ("press", "Да, однозначно!"), ("press", "9"), ("press", "7"),
              ("say", "Думал, что на линии будет больше обучения"), ("press", "Да"), ("press", "9"),
              ("press", "8"), ("press", "10"), ("press", "8"), ("press", "9"), ("press", "9"),
              ("say", "Добавить в первый день экскурсию по цеху с мастером")],
}

STAGE_TITLES = {
    "setup": "Подготовка: HR завёл новичка, команда подключилась к боту",
    "onboarding": "Приглашение и знакомство",
    "day0": "Накануне выхода",
    "day1": "Первый рабочий день",
    "day1-question": "Первый рабочий день: свой вопрос",
    "day2": "Второй рабочий день: «представлю тебя коллегам»",
    "day3": "Третий рабочий день: встреча с руководителем и опрос",
    "day3-red": "Третий рабочий день: тревожное сообщение",
    "day3-hr": "Третий рабочий день: HR отвечает",
    "day14": "Две недели: чек второй недели",
    "day60": "Два месяца",
    "end": "Конец испытательного срока: итоговый опрос и прощание",
}

_events = itertools.count(1)


# --- события «МАКС» ------------------------------------------------------------------------

def ev_started(p: Person, payload: str, user: dict | None = None) -> dict:
    return {"update_type": "bot_started", "chat_id": p.chat_id, "payload": payload,
            "user": user or {"user_id": p.user_id, "name": p.full_name}}


def ev_text(p: Person, text: str, attachments: list[dict] | None = None) -> dict:
    body = {"mid": f"in{next(_events)}", "text": text}
    if attachments:
        body["attachments"] = attachments
    return {"update_type": "message_created",
            "message": {"sender": {"user_id": p.user_id, "name": p.full_name},
                        "recipient": {"chat_id": p.chat_id, "chat_type": "dialog"}, "body": body}}


def ev_bot_added(chat_id: int, by: Person) -> dict:
    return {"update_type": "bot_added", "chat_id": chat_id, "user": {"user_id": by.user_id}, "is_channel": False}


def buttons(atts: list[dict] | None) -> list[dict]:
    return [b for a in atts or [] if a.get("type") == "inline_keyboard"
            for row in (a.get("payload") or {}).get("buttons") or [] for b in row]


def media(atts: list[dict] | None) -> list[str]:
    """Вложения сообщения: имя файла из токена фейковой загрузки (img-<имя> / file-<имя>)."""
    out = []
    for a in atts or []:
        if a.get("type") in ("image", "file", "video", "audio"):
            token = str((a.get("payload") or {}).get("token") or "")
            out.append(re.sub(r"^(img|file)-", "", token) or a["type"])
    return out


def ev_press(api, p: Person, mid: str, label: str) -> dict:
    msg = api.msgs.get(mid) or {}
    btns = buttons(msg.get("attachments"))
    btn = next((b for b in btns if b.get("type") == "callback"
                and (b["text"] == label or b["text"].removeprefix("✓ ") == label)), None)
    if btn is None:
        raise AssertionError(f"Нет кнопки «{label}» в сообщении {mid}: {[b.get('text') for b in btns]}")
    return {"update_type": "message_callback",
            "callback": {"callback_id": f"{mid}|{next(_events)}", "payload": btn["payload"],
                         "user": {"user_id": p.user_id}},
            "message": {"recipient": {"chat_id": p.chat_id, "chat_type": "dialog"},
                        "body": {"mid": mid, "text": msg.get("text") or "", "attachments": msg.get("attachments")}}}


# --- снимок БД -------------------------------------------------------------------------------

async def snapshot(s) -> dict[str, dict[int, dict]]:
    """Записи, которые меняет путь новичка: по таблицам {id: поля}."""
    out: dict[str, dict[int, dict]] = {}
    rows = (await s.execute(select(Delivery, ScenarioStep)
                            .outerjoin(ScenarioStep, ScenarioStep.id == Delivery.step_id))).all()
    out["deliveries"] = {d.id: {"employee_id": d.employee_id, "step": r.order_no if r else None,
                                "sites": list(r.site_tags or []) if r else [],
                                "office_only": bool((r.extra or {}).get("office_only")) if r else False,
                                "gate": (r.extra or {}).get("gate") if r else None,
                                "status": d.status, "error": d.error, "key": d.idempotency_key, "mid": d.mid}
                         for d, r in rows}
    qs = {q.id: q for q in (await s.scalars(select(SurveyQuestion))).all()}
    codes = {v.id: v.code for v in (await s.scalars(select(Survey))).all()}
    out["answers"] = {}
    for a in (await s.scalars(select(SurveyAnswer))).all():
        q = qs.get(a.question_id)
        out["answers"][a.id] = {"employee_id": a.employee_id, "survey": codes.get(q.survey_id) if q else None,
                                "key": (q.branches or {}).get("_key") if q else None, "value": a.value,
                                "score": a.score, "text": a.text, "mood": a.mood, "topic": a.topic}
    out["flags"] = {f.id: {"employee_id": f.employee_id, "level": f.level, "topic": f.topic, "source": f.source, "ticket_id": f.ticket_id,
                           "answer_id": f.answer_id, "status": f.status, "quote": f.quote}
                    for f in (await s.scalars(select(Flag))).all()}
    out["tickets"] = {t.id: {"employee_id": t.employee_id, "source": t.source, "category": t.category, "status": t.status,
                             "assignee_id": t.assignee_id, "text": t.text, "group_mid": t.group_mid,
                             "answered": t.first_response_at is not None, "is_personal": t.is_personal}
                      for t in (await s.scalars(select(Ticket))).all()}
    out["tasks"] = {t.id: {"employee_id": t.employee_id, "day": t.day, "kind": t.kind, "status": t.status, "mid": t.mid,
                           "due": t.due_date.isoformat() if t.due_date else None, "manager_id": t.manager_id}
                    for t in (await s.scalars(select(ManagerTask))).all()}
    out["questions"] = {q.id: {"employee_id": q.employee_id, "route": q.route, "chunk_id": q.chunk_id, "ticket_id": q.ticket_id,
                               "question": q.question, "topic": q.topic}
                        for q in (await s.scalars(select(QuestionLog))).all()}
    out["messages"] = {m.id: {"employee_id": m.employee_id, "staff_id": m.staff_id, "direction": m.direction,
                              "ticket_id": m.ticket_id, "text": m.text, "chat_id": m.chat_id}
                       for m in (await s.scalars(select(Message))).all()}
    out["consents"] = {c.id: {"purpose": c.purpose, "given": c.given}
                       for c in (await s.scalars(select(Consent))).all()}
    return out


# --- этап пути ---------------------------------------------------------------------------------

@dataclass
class Stage:
    key: str
    title: str
    start: int = 0                  # индекс в api.log
    end: int = 0
    day: int | None = None          # день адаптации после этапа
    vdate: date | None = None       # дата «сейчас» у новичка
    error: str | None = None
    travel: dict | None = None      # ответ time_travel
    db_before: dict = field(default_factory=dict)
    db_after: dict = field(default_factory=dict)

    def new(self, table: str) -> dict[int, dict]:
        before = self.db_before.get(table) or {}
        return {i: r for i, r in (self.db_after.get(table) or {}).items() if i not in before}

    def changed(self, table: str) -> list[tuple[int, dict, dict]]:
        before = self.db_before.get(table) or {}
        return [(i, before[i], r) for i, r in (self.db_after.get(table) or {}).items()
                if i in before and before[i] != r]


class LogCapture(logging.Handler):
    """Предупреждения и ошибки из логов приложения: проглоченные исключения тоже видны."""

    def __init__(self, run: Journey):
        super().__init__(level=logging.WARNING)
        self.run = run

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + "".join(traceback.format_exception(*record.exc_info))[-3000:]
        st = self.run.current
        self.run.logs.append({"stage": st.key if st else None, "level": record.levelname,
                              "logger": record.name, "msg": msg})


async def _no_sleep(_):
    return None


class Journey:
    def __init__(self, factory, api):
        self.factory = factory
        self.api = api
        self.ctx = BotCtx(sender=Sender(api, per_chat_rate=1000, global_rate=1000, sleep=_no_sleep),
                          bot_name=settings.max_bot_name, miniapp_url=settings.miniapp_url)
        self.stages: list[Stage] = []
        self.hidden: list[Stage] = []
        self.current: Stage | None = None
        self.final: dict = {}
        self.edge_ids: dict[str, int] = {}
        self.logs: list[dict] = []
        self.plugins: list[str] = []
        self.emp_id: int | None = None
        self.staff_ids: dict[str, int] = {}
        self.chunk_id: int | None = None
        self.kb_queries: list[str] = []
        self.llm_calls: list[str] = []
        self.soon: list[int] = []
        self.handled: set[str] = set()
        self.surveys_run: set[str] = set()
        self.inbound: list[str] = []
        self.verbatim: list[str] = []
        self.onboarding_mid: str | None = None
        self.import_report = None
        self._deliver_soon = engine._deliver_soon   # настоящая функция (в прогоне — после коммита)

    # --- подмены ---

    async def fake_search(self, session, query, *, site_code=None, limit=5, **kw) -> list[Hit]:
        """Поиск по базе знаний без эмбеддингов: на вопрос про обед — пункт ПВТР, иначе ничего."""
        self.kb_queries.append(query)
        if self.chunk_id is None or not re.search(r"обед|перерыв", query.lower()):
            return []
        ch = await session.get(KbChunk, self.chunk_id)
        return [Hit(chunk_id=ch.id, text=ch.text, source_label=ch.source_label or "", score=1 / 61, is_kt=False,
                    status="confirmed", sim=0.83, fts=0.4, fts_rank=1, vec_rank=1, chunk_key=ch.chunk_key,
                    title="Правила внутреннего трудового распорядка", kind="doc", path=ch.path,
                    point_no=ch.point_no)]

    def queue_deliver_soon(self, emp_id: int, delay: float = 1.5) -> None:
        self.soon.append(emp_id)

    async def flush_soon(self) -> None:
        """Отправка шагов после перемотки или отмашки: engine._deliver_soon, но после коммита вызывающего."""
        while self.soon:
            self._deliver_soon(self.soon.pop(0), delay=0)
        if engine._background:
            await asyncio.gather(*list(engine._background))

    # --- люди и бот ---

    def act(self, who: Person, text: str) -> None:
        self.api.log.append({"op": "act", "who": who.key, "text": text, "mid": None, "chat_id": None,
                             "attachments": []})

    async def feed(self, upd: dict) -> None:
        """Событие «МАКС» — как в боте: одна транзакция на событие (runner.process_update)."""
        await runner.process_update(self.ctx, upd)

    async def press(self, who: Person, mid: str | None, label: str) -> None:
        if mid is None:
            raise AssertionError(f"Нет сообщения с кнопкой «{label}»")
        upd = ev_press(self.api, who, mid, label)
        self.act(who, f"нажимает «{label}»")
        await self.feed(upd)

    async def say(self, who: Person, text: str, attachments: list[dict] | None = None, note: str | None = None):
        if who is NEWCOMER and text:
            self.inbound.append(text)
        self.act(who, note or f"пишет: «{text}»")
        await self.feed(ev_text(who, text, attachments))

    def sent(self, who: Person, start: int = 0) -> list[dict]:
        return [e for e in self.api.log[start:] if e["op"] == "send" and e["chat_id"] == who.chat_id]

    def last_sent(self, who: Person) -> dict:
        got = self.sent(who)
        if not got:
            raise AssertionError(f"{who.label}: сообщений нет")
        return got[-1]

    async def load_emp(self, s=None) -> Employee:
        if s is not None:
            return await s.scalar(select(Employee).options(selectinload(Employee.site))
                                  .where(Employee.id == self.emp_id))
        async with self.factory() as s2:
            return await self.load_emp(s2)

    # --- время и планировщик ---

    async def jobs(self) -> None:
        """Задачи планировщика, как в app/main.py: шаги сценария, push руководителю, напоминания об опросах."""
        from app.manager.hooks import manager_tasks
        from app.scenario.hooks import scenario_tick
        from app.surveys.hooks import survey_reminders

        await self.flush_soon()
        await scenario_tick()
        await manager_tasks()
        await survey_reminders()

    async def travel(self, to: str, emp_id: int | None = None, *, deliver: bool = True) -> dict:
        """Машина времени, как кнопка в Mini App (POST /api/demo/shift)."""
        at = len(self.api.log)  # push руководителю уходит внутри перемотки: отметка о перемотке — раньше него
        async with self.factory() as s:
            emp = await s.scalar(select(Employee).options(selectinload(Employee.site))
                                 .where(Employee.id == (emp_id or self.emp_id)))
            res = await engine.time_travel(s, emp, to)
            await s.commit()
        if self.current is not None and emp_id is None:
            self.current.travel = res
        self.act(CLOCK, f"машина времени: {res.get('message')}")
        self.api.log.insert(at, self.api.log.pop())
        if deliver:
            await self.flush_soon()
        else:
            self.soon.clear()
        return res

    async def advance_to(self, when: datetime) -> None:
        """Живое время: «сейчас» у новичка не раньше when (сдвиг часов, как у машины времени)."""
        shift = math.ceil((when - datetime.now(UTC)).total_seconds() / 3600)
        async with self.factory() as s:
            emp = await s.get(Employee, self.emp_id)
            emp.time_shift_hours = shift
            await s.commit()
        self.act(CLOCK, f"время идёт: у новичка {fmt_date(when.date())}")

    async def live_days(self, lo: int, hi: int) -> list[tuple[int, datetime]]:
        """Дни строго между lo и hi, когда по плану что-то происходит: шаги сценария или встреча руководителя.

        → [(день адаптации, «сейчас» после последнего шага этого дня, но не раньше 10:01)].
        """
        async with self.factory() as s:
            emp = await self.load_emp(s)
            plan = await engine.load_plan(s)
            ctx = engine.employee_ctx(emp, emp.site.tz)
            moments: dict[date, list[datetime]] = defaultdict(list)
            for p in plan_for(ctx, plan.specs_for(emp)):
                row = plan.by_number[p.step_number]
                if p.gate or (row.extra or {}).get("channel") == "email":
                    continue
                moments[p.due_at.date()].append(p.due_at)
            for t in (await s.scalars(select(ManagerTask).where(ManagerTask.employee_id == emp.id))).all():
                if t.due_date:
                    moments.setdefault(t.due_date, [])
            first = engine.first_day(emp)
        out = []
        for d in sorted(moments):
            day = (d - first).days + 1
            if lo < day < hi:
                land = [m + timedelta(minutes=1) for m in moments[d]]
                land.append(datetime.combine(d, time(10, 1), tzinfo=MSK))
                out.append((day, max(land)))
        return out

    # --- ответы новичка и руководителя ---

    async def run_survey(self, code: str) -> None:
        script = SURVEY_SCRIPTS.get(code)
        if script is None:
            self.act(NEWCOMER, f"не отвечает на опрос {code}: для него нет сценария ответов")
            return
        for kind, arg in script:
            emp = await self.load_emp()
            st = (emp.dialog_data or {}).get("survey") or {}
            if st.get("mid"):
                self.handled.add(st["mid"])
            if kind == "press":
                await self.press(NEWCOMER, st.get("mid"), arg)
            else:
                await self.say(NEWCOMER, arg)

    def _waiting(self, who: Person, prefix: str) -> list[dict]:
        out = []
        for e in self.sent(who):
            if e["mid"] in self.handled:
                continue
            btns = buttons((self.api.msgs.get(e["mid"]) or {}).get("attachments"))
            if any(str(b.get("payload") or "").startswith(prefix) for b in btns if b.get("type") == "callback"):
                out.append(e)
        return out

    async def respond(self) -> None:
        """Новичок проходит начатый опрос и жмёт кнопки шагов, руководитель жмёт «Провёл»."""
        for _ in range(5):
            acted = False
            emp = await self.load_emp()
            st = (emp.dialog_data or {}).get("survey") or {}
            code = st.get("code")
            if code and code not in self.surveys_run:
                self.surveys_run.add(code)
                await self.run_survey(code)
                acted = True
            for e in self._waiting(NEWCOMER, "sc:"):
                self.handled.add(e["mid"])
                labels = [b["text"] for b in buttons((self.api.msgs.get(e["mid"]) or {}).get("attachments"))]
                label = next((x for x in STEP_PRESSES if x in labels), None)
                if label:
                    await self.press(NEWCOMER, e["mid"], label)
                    acted = True
            for e in self._waiting(MANAGER, "mg:"):
                self.handled.add(e["mid"])
                await self.press(MANAGER, e["mid"], "Провёл")
                acted = True
            if not acted:
                return
            await self.jobs()

    # --- этапы ---

    @asynccontextmanager
    async def stage(self, key: str, title: str | None = None, *, hidden: bool = False):
        """Этап пути. hidden — проверка вне маршрута: в eval/journey/*.md не попадает."""
        st = Stage(key=key, title=title or STAGE_TITLES.get(key, key), start=len(self.api.log))
        (self.hidden if hidden else self.stages).append(st)
        self.current = st
        try:
            async with self.factory() as s:
                st.db_before = await snapshot(s)
            yield st
        except Exception as e:  # noqa: BLE001 — этап падает, прогон идёт дальше; тест этапа покажет ошибку
            st.error = "".join(traceback.format_exception(e))
        finally:
            st.end = len(self.api.log)
            try:
                async with self.factory() as s:
                    st.db_after = await snapshot(s)
                    emp = await s.get(Employee, self.emp_id) if self.emp_id else None
                    if emp is not None:
                        st.day = engine.adaptation_day(emp)
                        st.vdate = emp_date(emp)
            except Exception as e:  # noqa: BLE001
                st.error = (st.error or "") + "".join(traceback.format_exception(e))
            self.current = None

    def stage_by(self, key: str) -> Stage:
        return next(s for s in self.stages + self.hidden if s.key == key)

    async def setup(self) -> None:
        async with self.factory() as s:
            s.add_all([Site(code="GTP", name="GTP (синтетика)", tz="Europe/Moscow"),
                       Site(code="GrPP", name="GrPP (синтетика)", tz="Europe/Moscow"),
                       Site(code="Luzhniki", name="Лужники (синтетика)", tz="Europe/Moscow")])
            await s.flush()
            self.import_report = await store.import_xlsx(s, XLSX)
            staff = {p.key: Staff(full_name=p.full_name, role=p.role, site_code="GTP", bind_code=p.bind)
                     for p in STAFF}
            s.add_all(staff.values())
            await s.flush()
            text, page = pvtr_point("6.6.1")
            doc = KbDocument(title="Правила внутреннего трудового распорядка", doc_type="md", source="docs/pvtr.md",
                             site_codes=[], status="confirmed")
            s.add(doc)
            await s.flush()
            chunk = KbChunk(document_id=doc.id, path="6.6. Рабочее время", point_no="6.6.1",
                            chunk_key="pvtr:6.6.1", page=page, text=text,
                            source_label="ПВТР, п. 6.6.1" + (f", стр. {page}" if page else ""))
            s.add(chunk)
            emp = Employee(full_name=NEWCOMER.full_name, first_name="Иван", site_code="GTP", work_type="production",
                           start_date=START, probation_end=PROBATION_END, manager_id=staff["manager"].id,
                           recruiter_id=staff["recruiter"].id, invite_code=INVITE, status="waiting")
            s.add(emp)
            await s.commit()
            self.emp_id, self.chunk_id = emp.id, chunk.id
            self.staff_ids = {k: v.id for k, v in staff.items()}
        self.act(HR, "добавляет новичка в Mini App: Смирнов Иван, GTP, производство, выход 9 ноября")
        for p in STAFF:
            self.act(p, "подключается к боту по ссылке из панели")
            await self.feed(ev_started(p, p.bind))
        self.act(HR, "добавляет бота в HR-группу «МАКС»")
        await self.feed(ev_bot_added(HR_GROUP.chat_id, HR))

    async def run(self) -> None:
        async with self.stage("setup"):
            await self.setup()

        async with self.stage("onboarding"):
            # «сейчас» у новичка — за 3 дня до выхода, когда HR отправил приглашение
            await self.travel("-2")
            self.act(NEWCOMER, "открывает ссылку-приглашение в «МАКС»")
            await self.feed(ev_started(NEWCOMER, INVITE, {"user_id": NEWCOMER.user_id, "name": "Иван Смирнов",
                                                          "first_name": "Иван", "last_name": "Смирнов"}))
            self.onboarding_mid = self.last_sent(NEWCOMER)["mid"]
            for label in ("На ТЫ", "Подробнее", "Даю согласие", "Можно"):
                await self.press(NEWCOMER, self.onboarding_mid, label)
            self.handled.add(self.onboarding_mid)
            await self.jobs()
            await self.respond()

        async with self.stage("day0"):
            await self.advance_to(datetime.combine(START - timedelta(days=1), time(10, 1), tzinfo=MSK))
            await self.jobs()
            await self.respond()

        async with self.stage("day1"):
            await self.travel("1")
            await self.jobs()
            await self.respond()

        async with self.stage("day1-question"):
            await self.say(NEWCOMER, QUESTION)
            card = self.last_sent(NEWCOMER)
            self.handled.add(card["mid"])
            self.act(NEWCOMER, "работает по сменам, офисный график ему не подходит")
            await self.press(NEWCOMER, card["mid"], "Связаться с HR")
            await self.jobs()

        async with self.stage("day2"):
            await self.advance_to(datetime.combine(START + timedelta(days=1), time(10, 1), tzinfo=MSK))
            await self.jobs()
            async with self.factory() as s:
                emp = await self.load_emp(s)
                await engine.open_gate(s, emp, "recruiter_go")
                await s.commit()
            self.act(RECRUITER, "даёт отмашку «представлю тебя коллегам» в Mini App")
            await self.flush_soon()
            await self.say(NEWCOMER, INTRO_TEXT)
            await self.say(NEWCOMER, "", PHOTO, note="присылает фото")
            await self.jobs()
            await self.respond()

        async with self.stage("day3"):
            await self.travel("3")
            await self.jobs()
            await self.respond()

        async with self.stage("day3-red"):
            await self.say(NEWCOMER, RED_TEXT)
            await self.jobs()

        async with self.stage("day3-hr"):
            from app.tickets import service as tickets

            async with self.factory() as s:
                ticket = await s.scalar(select(Ticket).where(Ticket.source == "flag").order_by(Ticket.id.desc()))
                hr = await s.get(Staff, self.staff_ids["hr"])
                self.act(HR, f"отвечает в Mini App на обращение #{ticket.id}: «{HR_REPLY}»")
                await tickets.reply(s, ticket, hr, HR_REPLY)
                await s.commit()
            await self.say(NEWCOMER, FOLLOWUP)
            await self.jobs()

        async with self.stage("day14"):
            await self.travel("14")
            await self.jobs()
            await self.respond()

        lo = self.stage_by("day14").day or 14
        for day, when in await self.live_days(lo, 60):
            async with self.stage(f"live{day}", "Без перемотки: что приходит в этот день"):
                await self.advance_to(when)
                await self.jobs()
                await self.respond()

        async with self.stage("day60"):
            await self.travel("60")
            await self.jobs()
            await self.respond()

        lo = self.stage_by("day60").day or 60
        end_day = (PROBATION_END - START).days + 1
        for day, when in await self.live_days(lo, end_day):
            async with self.stage(f"live{day}", "Без перемотки: что приходит в этот день"):
                await self.advance_to(when)
                await self.jobs()
                await self.respond()

        async with self.stage("end"):
            await self.travel("end")
            await self.jobs()
            await self.respond()

        async with self.factory() as s:
            emp = await s.get(Employee, self.emp_id)
            self.final = {"status": emp.status, "address_form": emp.address_form, "dialog_state": emp.dialog_state,
                          "dialog_data": dict(emp.dialog_data or {})}
        await self.collect_verbatim()
        await self.edge_cases()

    # --- проверки вне маршрута ---

    async def add_connected(self, p: Person) -> int:
        """Новичок GTP, уже прошедший знакомство (на «ты»), без перемотки времени."""
        async with self.factory() as s:
            emp = Employee(full_name=p.full_name, first_name=p.label, site_code="GTP", work_type="production",
                           start_date=START, probation_end=PROBATION_END, manager_id=self.staff_ids["manager"],
                           recruiter_id=self.staff_ids["recruiter"], invite_code=f"INV-{p.key}", status="active",
                           address_form="ty", max_user_id=p.user_id, chat_id=p.chat_id,
                           dialog_data={"onboarded": True})
            s.add(emp)
            await s.commit()
            self.edge_ids[p.key] = emp.id
            return emp.id

    async def hr_reply_last(self, emp_id: int, text: str) -> int:
        from app.tickets import service as tickets

        async with self.factory() as s:
            ticket = await s.scalar(select(Ticket).where(Ticket.employee_id == emp_id).order_by(Ticket.id.desc()))
            await tickets.reply(s, ticket, await s.get(Staff, self.staff_ids["hr"]), text)
            await s.commit()
            return ticket.id

    async def edge_cases(self) -> None:
        # 1. HR ответил на обычное обращение, через пару минут новичок пишет об угрозе
        async with self.stage("edge-window-red", "Красный флаг в окне ответа HR", hidden=True):
            emp_id = await self.add_connected(PETR)
            await self.say(PETR, "/hr")
            await self.say(PETR, "Где получить спецодежду и пропуск на территорию?")
            await self.hr_reply_last(emp_id, "Пётр, спецодежду выдают на складе у проходной №2 с 8:00.")
            await self.say(PETR, EDGE_RED)
        # 2. HR ответил, ведущий демо перемотал время на две недели, новичок задаёт новый вопрос
        async with self.stage("edge-window-travel", "Окно ответа HR после перемотки", hidden=True):
            emp_id = await self.add_connected(ANNA)
            await self.say(ANNA, "/hr")
            await self.say(ANNA, "Как оформить пропуск на парковку?")
            await self.hr_reply_last(emp_id, "Анна, пропуск на парковку оформляет охрана, нужен номер машины.")
            await self.travel("14", emp_id=emp_id, deliver=False)
            await self.say(ANNA, QUESTION)

    # --- проверки, общие для теста и маршрута ---

    async def collect_verbatim(self) -> None:
        """Тексты, которые бот пересылает дословно: шаги и опросы из ТЗ, пункт ПВТР, слова людей."""
        from app.surveys import config as C

        out: list[str] = []
        async with self.factory() as s:
            emp = await s.get(Employee, self.emp_id)
            chunk = await s.get(KbChunk, self.chunk_id)
            for row in (await s.scalars(select(ScenarioStep))).all():
                out += [row.text or "", engine.personalize(row.text or "", emp)]
                for b in row.buttons or []:
                    out += [b.get("text") or "", engine.personalize(b.get("reply") or "", emp)]
            out += (chunk.text or "").splitlines()
        name = re.compile(r"(?<![А-Яа-яЁёA-Za-z])Х(?![А-Яа-яЁёA-Za-z])")
        for sv in C.load().values():
            for t in (sv.intro, sv.outro, sv.title):
                out.append(name.sub("Иван", t or ""))
            for q in sv.questions:
                out += [name.sub("Иван", q.text), q.legend or ""]
                for o in q.options:
                    out += [o.label, name.sub("Иван", o.reply or ""), *(a.label for a in o.actions)]
        out += self.inbound + [HR_REPLY]
        self.verbatim = sorted({t.strip() for t in out if t and t.strip()}, key=len, reverse=True)

    def bot_entries(self, stage: Stage | None = None) -> list[dict]:
        log = self.api.log[stage.start:stage.end] if stage else self.api.log
        return [e for e in log if e["op"] in ("send", "answer", "edit", "toast")]

    def dash_violations(self) -> list[str]:
        """Длинное тире «—» в собственных фразах бота (дословные тексты ТЗ и цитаты не считаются)."""
        out = []
        for st in self.stages:
            for e in self.bot_entries(st):
                rest = e["text"] or ""
                for frag in self.verbatim:
                    if frag in rest:
                        rest = rest.replace(frag, "")
                rest = "\n".join(ln for ln in rest.splitlines() if not ln.lstrip().startswith(">"))
                if "—" in rest:
                    who = BY_CHAT.get(e["chat_id"], HR_GROUP).label
                    out.append(f"{st.key} → {who}: {short(e['text'], 200)}")
        return out

    def manager_leaks(self) -> list[str]:
        """Что из ответов опросов и обращений попало руководителю (должно быть пусто)."""
        private = [t for t in self.inbound if len(t) >= 8]
        # ответы опросов фразами («Пока ничего не понял»), не отдельными словами вроде «Руководитель»
        private += [arg for script in SURVEY_SCRIPTS.values() for _, arg in script if " " in arg and len(arg) >= 8]
        words = ("обращение #", "опрос «", "красный флаг", "кожух", "угрожа")
        out = []
        for e in self.bot_entries():
            if e["chat_id"] != MANAGER.chat_id:
                continue
            low = (e["text"] or "").lower()
            if any(p.lower() in low for p in private) or any(w in low for w in words):
                out.append(short(e["text"], 200))
        return out

    def counts(self, stage: Stage) -> dict[str, Counter]:
        """{получатель: Counter(new=…, edit=…)} за этап."""
        out: dict[str, Counter] = defaultdict(Counter)
        for e in self.bot_entries(stage):
            who = BY_CHAT.get(e["chat_id"])
            key = who.key if who else f"chat:{e['chat_id']}"
            out[key]["new" if e["op"] == "send" else "edit"] += 1
        return out

    def errors(self, stage: Stage | None = None) -> list[str]:
        """Исключения этапа и ошибки из логов приложения (в том числе проглоченные)."""
        out = []
        for st in [stage] if stage else self.stages:
            if st.error:
                out.append(f"{st.key}: {st.error}")
            out += [f"{st.key}: {r['logger']}: {r['msg']}" for r in self.logs
                    if r["stage"] == st.key and r["level"] in ("ERROR", "CRITICAL")]
        return out

    def warnings(self, stage: Stage | None = None) -> list[str]:
        keys = {stage.key} if stage else {s.key for s in self.stages}
        return [f"{r['stage']}: {r['logger']}: {r['msg']}" for r in self.logs
                if r["stage"] in keys and r["level"] == "WARNING"]

    # --- маршрут для людей ---

    def write_md(self) -> Path:
        MD_PATH.parent.mkdir(parents=True, exist_ok=True)
        MD_PATH.write_text(render_md(self), encoding="utf-8")
        return MD_PATH


# --- вспомогательное ------------------------------------------------------------------------------

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря")


def fmt_date(d: date | None, year: bool = False) -> str:
    if d is None:
        return "?"
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if year else "")


def emp_date(emp) -> date:
    return (datetime.now(UTC) + timedelta(hours=emp.time_shift_hours or 0)).astimezone(MSK).date()


def day_label(day: int | None) -> str:
    if day is None:
        return "до старта"
    if day == 0:
        return "Накануне выхода"
    if day < 0:
        return f"За {1 - day} дня до выхода"
    return f"День {day}"


def pvtr_point(no: str) -> tuple[str, int | None]:
    """Дословный пункт ПВТР из docs/pvtr.md и страница; без файла — синтетический текст."""
    if PVTR.exists():
        page = None
        for line in PVTR.read_text(encoding="utf-8").splitlines():
            m = re.match(r"<!--\s*стр\.\s*(\d+)\s*-->", line.strip())
            if m:
                page = int(m.group(1))
            elif line.startswith(f"{no}. "):
                return line.strip(), page
    return f"{no}. Перерыв для отдыха и питания: 13:00 – 13:48 (синтетика).", None


def short(text: str | None, limit: int = 170, tail: bool = False) -> str:
    """1–2 строки текста: начало для нового сообщения, конец для правки на месте."""
    lines = [ln.strip().replace("**", "") for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln[1:].strip() if ln.startswith(">") else ln for ln in lines]
    pick = lines[-2:] if tail else lines[:2]
    out = " / ".join(pick)
    if len(out) > limit:
        out = (out[:limit].rstrip() + "…") if not tail else ("…" + out[-limit:].lstrip())
    elif len(lines) > 2:
        out = ("… " + out) if tail else (out + " …")
    return out.replace("|", "/")


def _kb(atts: list[dict] | None) -> str:
    labels = [b.get("text") for b in buttons(atts)]
    files = media(atts)
    parts = []
    if labels:
        parts.append(" ".join(f"[{t}]" for t in labels))
    if files:
        parts.append(" ".join(f"📎 {f}" for f in files))
    return (" " + " ".join(parts)) if parts else ""


def render_entry(e: dict) -> str:
    if e["op"] == "act":
        who = BY_KEY.get(e["who"], NEWCOMER)
        return f"{who.icon} **{who.label}** {e['text']}"
    who = BY_CHAT.get(e["chat_id"])
    to = who.label if who else f"чат {e['chat_id']}"
    if e["op"] == "send":
        body = f"«{short(e['text'])}»" if (e["text"] or "").strip() else "(без текста)"
        return f"🤖 → **{to}**: {body}{_kb(e['attachments'])}"
    if e["op"] == "toast":
        return f"💬 {to}: всплывающее «{e['text']}»"
    return f"✎ {to}, то же сообщение: «{short(e['text'], tail=True)}»{_kb(e['attachments'])}"


SOURCE_LABELS = {"button": "кнопка «Связаться с HR»", "flag": "красный флаг", "survey": "опрос",
                 "unknown": "бот не знает ответ", "personal": "личный вопрос", "hr_command": "команда /hr"}
CONSENTS = {"adaptation": "согласие на сопровождение", "ai_analysis": "согласие на ИИ-разбор ответов"}


def _answer(a: dict) -> str:
    val = a["value"] if a["score"] is None else str(a["score"])
    out = f"«{val}»" if val else ""
    if a["text"]:
        out += (" + " if out else "") + f"текст «{short(a['text'], 80)}»"
    return out + (f" ({a['mood']})" if a.get("mood") else "")


def records(st: Stage) -> list[str]:
    """Что появилось в БД за этап: человеческим языком."""
    from app.surveys import config as C
    from app.tickets.texts import CATEGORY_LABELS

    out = []
    c = st.new("consents")
    if c:
        out.append(", ".join(f"{CONSENTS.get(x['purpose'], x['purpose'])} {'✓' if x['given'] else '✗'}"
                             for x in c.values()))
    answers = defaultdict(list)
    for a in st.new("answers").values():
        answers[a["survey"]].append(a)
    for code, items in answers.items():
        sv = C.get(code) if code else None
        out.append(f"ответы опроса «{sv.title if sv else code}»: " + "; ".join(_answer(a) for a in items))
    for i, f in st.new("flags").items():
        icon = "🔴" if f["level"] == "red" else "🟡"
        link = f" → обращение #{f['ticket_id']}" if f["ticket_id"] else ", без обращения"
        out.append(f"флаг {icon} «{f['topic']}» (источник: {f['source']}){link}")
    for i, t in st.new("tickets").items():
        label = CATEGORY_LABELS.get(t["category"], t["category"] or "")
        out.append(f"обращение #{i}: {label}, {SOURCE_LABELS.get(t['source'], t['source'])}, статус {t['status']}")
    for i, b, a in st.changed("tickets"):
        if b["status"] != a["status"]:
            out.append(f"обращение #{i}: {b['status']} → {a['status']}")
    for i, q in st.new("questions").items():
        out.append(f"вопрос в базу знаний: «{q['question']}» → {q['route']}")
    for i, b, a in st.changed("questions"):
        if b["route"] != a["route"]:
            out.append(f"вопрос «{a['question']}»: {b['route']} → {a['route']}, обращение #{a['ticket_id']}")
    tasks = st.new("tasks")
    if tasks:
        out.append("встречи руководителя: " + ", ".join(
            f"{'итоговая' if t['kind'] == 'probation' else 'день ' + str(t['day'])} ({t['due']})"
            for t in sorted(tasks.values(), key=lambda x: x["due"] or "")))
    for i, b, a in st.changed("tasks"):
        name = "итоговая встреча" if a["kind"] == "probation" else f"встреча дня {a['day']}"
        if not b["mid"] and a["mid"]:
            out.append(f"{name}: push руководителю отправлен")
        if b["status"] != a["status"]:
            out.append(f"{name}: {b['status']} → {a['status']}")
    linked = [m for m in st.new("messages").values() if m["direction"] == "in" and m["ticket_id"]]
    for m in linked:
        out.append(f"сообщение новичка «{short(m['text'], 60)}» → в обращение #{m['ticket_id']}")
    return out


def deliveries_line(st: Stage) -> str | None:
    groups: dict[str, list[str]] = defaultdict(list)
    for d in sorted(st.new("deliveries").values(), key=lambda x: x["step"] or 0):
        note = d["error"] or ""
        if d["status"] == "sent" and note == engine.COVERED_NOTE:
            k = "покрыты знакомством"
        elif d["status"] == "sent" and ":b" in d["key"]:
            k = "по кнопке"
        elif d["status"] == "sent":
            k = "отправлены"
        elif note == engine.TRAVEL_NOTE:
            k = "пропущены перемоткой"
        elif note == engine.EMAIL_NOTE:
            k = "письмом, не в боте"
        else:
            k = f"{d['status']} ({note})" if note else d["status"]
        groups[k].append(str(d["step"]))
    if not groups:
        return None
    return "; ".join(f"{k}: {', '.join(v)}" for k, v in groups.items())


def render_md(run: Journey) -> str:
    L: list[str] = []
    w = L.append
    w("# Путь сотрудника: оператор производства, площадка GTP")
    w("")
    w("Маршрут сгенерирован сквозным тестом `tests/e2e/test_e2e_journey.py` по настоящему коду бота: сценарий "
      "из xlsx, опросы, база знаний, сигналы, обращения, задачи руководителя. API «МАКС» фейковый, база тестовая, "
      "время двигает машина времени. **Все люди и данные синтетические.**")
    w("")
    w("Обновить: `docker compose exec -T app pytest -q tests/e2e`.")
    w("")
    w("## Кто участвует")
    w("")
    w(f"- **Новичок:** {NEWCOMER.full_name}, оператор производства, площадка GTP. Выход: {fmt_date(START, True)}, "
      f"испытательный срок 3 месяца, до {fmt_date(PROBATION_END, True)}.")
    w(f"- **HR:** {HR.full_name}. **Рекрутер:** {RECRUITER.full_name}, закреплена за новичком.")
    w(f"- **Руководитель:** {MANAGER.full_name}. **Глава подбора:** {HEAD.full_name}.")
    w("- **HR-группа** в «МАКС»: сюда приходят обращения и красные флаги.")
    w("")
    w("Обозначения: 🤖 → новое сообщение бота, ✎ то же сообщение изменено на месте (бот не плодит сообщения), "
      "👤 / 🧑‍💼 / 👔 действия людей, ⏩ время, [Кнопка], 📎 вложение.")
    w("")
    w("## Сводка")
    w("")
    w("| Этап | Дата у новичка | Новичку: новых / правок | HR-группе | Руководителю | Записи |")
    w("|---|---|---|---|---|---|")
    for st in run.stages:
        if st.key == "setup":
            continue
        c = run.counts(st)
        n, g, m = c.get("newcomer", Counter()), c.get("hr_group", Counter()), c.get("manager", Counter())
        rec = records(st)
        w(f"| {day_label(st.day)}: {st.title} | {fmt_date(st.vdate)} | {n['new']} / {n['edit']} | {g['new']} | "
          f"{m['new']} | {len(rec)} |")
    w("")
    for st in run.stages:
        head = "Подготовка" if st.key == "setup" else f"{day_label(st.day)} · {fmt_date(st.vdate)}"
        w(f"## {head} · {st.title}")
        w("")
        for e in run.api.log[st.start:st.end]:
            w(f"- {render_entry(e)}")
        if st.error:
            w(f"- ⚠️ **ошибка этапа:** `{st.error.strip().splitlines()[-1]}`")
        rec = records(st)
        dl = deliveries_line(st)
        if rec or dl:
            w("")
            w("**Записи:**")
            w("")
            for r in rec:
                w(f"- {r}")
            if dl:
                w(f"- шаги сценария: {dl}")
        w("")
    w("## Проверки прогона")
    w("")
    errs, dashes, leaks = run.errors(), run.dash_violations(), run.manager_leaks()
    w(f"- Исключения и ошибки в логах: {'нет' if not errs else len(errs)}")
    w(f"- Длинное тире «—» в собственных фразах бота: {'нет' if not dashes else len(dashes)}")
    w(f"- Руководителю ушли ответы опросов или обращения: {'нет' if not leaks else len(leaks)}")
    w(f"- Обращения к LLM: {'нет' if not run.llm_calls else ', '.join(run.llm_calls)}")
    for x in errs + dashes + leaks:
        w(f"  - {short(x, 300)}")
    w("")
    return "\n".join(L)
