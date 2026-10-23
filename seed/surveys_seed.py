"""Синтетические ответы опросов, флаги и обращения из них (все данные синтетические).

Запуск: python -m seed.surveys_seed [--reset] [--thin SITE]
  --reset      удалить прежние синтетические ответы, их флаги и обращения и создать заново;
  --thin SITE  у площадки SITE оставить 4 ответивших за 4 недели (показать «мало данных» в сводке).

Источник текстов — 120 синтетических ответов Ивана (ветка ideas/ivan, docs/ideas/07-survey-answers-synth.jsonl,
копия — eval/flags/survey-synth.jsonl). Ответы раскладываются по синтетическим новичкам их площадки
(сначала make seed), до которых этот опрос уже дошёл по сценарию: опрос 3-го дня — на 3-й рабочий день,
итоговый — в последний день испытательного и т. д. (`SURVEY_TIMING`). Ответ — в день опроса, вскоре после него.
Новичок отвечает на вопрос один раз; если подходящих новичков нет, ответ пропускается. Красные и негативные
ответы раскладываются первыми, чтобы при нехватке новичков пропадали спокойные, а не сигналы.
К каждому новичку добавляются ответы кнопками на опросы, которые до него дошли (3-й день, 2-я неделя,
план, два месяца, итоговый), чтобы у сводки были оценки и порог «5 разных новичков».
Флаги — словарём app/signals/rules.py и кнопками-триггерами из config/surveys.yaml.
В HR-группу ничего не отправляется.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app.config import settings
from app.db.models import Employee, Flag, SurveyAnswer, Ticket
from app.db.session import async_session, engine
from app.scenario.models import TimingRule
from app.scenario.schedule import add_months, step_date
from app.signals import rules
from app.signals.service import TICKET_CATEGORY
from app.surveys import config as C
from app.surveys import store
from seed.risks_seed import ensure_stories, is_story

SYNTH = Path(__file__).resolve().parents[1] / "eval" / "flags" / "survey-synth.jsonl"
MOOD = {"negative": "negative", "neutral": "neutral", "positive": "positive"}

# Когда приходит опрос — как шаги сценария (config/surveys.yaml → номера шагов, время — колонка ТЗ):
# day1 — wd1@12:00, day3 — wd3@17:30, week2 — через 2 недели, plan — первый день 2-го месяца,
# month2 — через 1 месяц 3 недели, final — последний день испытательного. Даты — scenario.schedule.step_date.
SURVEY_TIMING = {
    "day1": TimingRule(kind="workday", n=1, time="12:00"),
    "day3": TimingRule(kind="workday", n=3, time="17:30"),
    "week2": TimingRule(kind="calendar_offset", weeks=2, time="10:00"),
    "plan": TimingRule(kind="month_start", month=2, time="09:00"),
    "month2": TimingRule(kind="calendar_offset", months=1, weeks=3, time="09:01"),
    "final": TimingRule(kind="probation_end", offset_days=0, time="09:01"),
}
BASE_SURVEYS = ("day3", "week2", "plan", "month2", "final")  # ответы кнопками у каждого, до кого дошёл опрос

# Подсказка кластера у Ивана → тема из app.llm.gigachat.TOPICS
TOPIC = {
    "руководитель": "руководитель", "встреча": "коллектив", "оформление": "документы", "задачи": "понимание задач",
    "задачи ИС": "понимание задач", "ИС": "понимание задач", "информация": "понимание задач",
    "доступы": "доступы", "техника": "доступы", "план адаптации": "обучение", "адаптация": "обучение",
    "онбординг": "обучение", "обучение": "обучение", "нагрузка": "нагрузка", "выгорание": "нагрузка",
    "усталость": "нагрузка", "переработки": "нагрузка", "график": "нагрузка", "наставник": "наставник",
    "наставничество": "наставник", "коллеги": "коллектив", "токсичность": "коллектив", "поддержка": "коллектив",
    "межфункциональное взаимодействие": "коллектив", "межфункциональное взаимодействие (сарказм)": "коллектив",
    "охрана труда": "охрана труда", "СИЗ": "охрана труда", "шум": "охрана труда", "условия труда": "охрана труда",
    "условия": "охрана труда", "быт": "быт и транспорт", "транспорт": "быт и транспорт",
    "логистика": "быт и транспорт", "навигация": "быт и транспорт",
}

# Вопрос у Ивана → (опрос, ключ вопроса, вариант или None — по метке настроения)
SCALE_MAP = {
    "w2_access_comment": "w2_access", "w2_manager_comment": "w2_manager", "w2_colleagues_comment": "w2_colleagues",
    "w2_manager_feedback": "w2_feedback", "w2_hr_comment": "w2_hr", "fin_scale_load": "fin_load",
    "fin_scale_expectations": "fin_expectations", "fin_scale_hr": "fin_hr", "fin_scale_tasks": "fin_goals",
    "fin_scale_manager": "fin_manager", "fin_scale_colleagues": "fin_colleagues", "fin_scale_depts": "fin_depts",
}


def _target(row: dict, site: str, rnd: random.Random) -> tuple[str, str, str | None, int | None]:
    """(код опроса, ключ вопроса, вариант, оценка) для синтетического ответа Ивана."""
    qid, _, qtext = row["question"].partition(":")
    label = row["label"]
    if qid in SCALE_MAP:
        key = SCALE_MAP[qid]
        code = "week2" if key.startswith("w2_") else "final"
        score = rnd.randint(2, 4) if label == "negative" else rnd.randint(5, 7)
        return code, key, str(score), score
    if qid == "d1_access":
        return "day1", "d1_access_gtp" if site == "GTP" else "d1_access", "Нет", None
    if qid == "d3_docs":
        return "day3", "d3_docs", "Возникли трудности с документами", None
    if qid == "d3_colleagues":
        value = {"negative": "Никак, все заняты", "neutral": "Пока присматриваюсь"}.get(label, "Отлично!")
        return "day3", "d3_colleagues", value, None
    if qid == "d3_tasks":
        value = "Пока ничего не понял" if "ничего не понял" in qtext else "Жду разъяснений от руководителя"
        return "day3", "d3_tasks", value, None
    if qid == "w2_general":
        return "week2", "w2_info", "Нет" if label == "negative" else "Да", None
    if qid == "w2_info_comment":
        return "week2", "w2_info", "Нет", None
    if qid == "m1_plan":
        if "Всё хорошо" in qtext:
            value = "Все хорошо! Продвигаюсь по плану"
        elif "Немного" in qtext:
            value = "Все ок! Немного отстаю от графика"
        else:
            value = "Я отстаю от графика."
        return "plan", "m1_plan", value, None
    if qid == "m2_mood_comment":
        value = {"negative": "Уже устал, а задачи все валятся",
                 "neutral": "С безразличным: поработал и ушел"}.get(label, "С отличным! Мне все нравится!")
        return "month2", "m2_mood", value, None
    if qid == "m2_speedup_own":
        return "month2", "m2_speedup", "Свой вариант", None
    if qid == "m2_helpers_comment":
        return "month2", "m2_helpers", "Никто!" if "Никто" in qtext else rnd.choice(["Коллеги", "Руководитель"]), None
    if qid == "fin_improve":
        return "final", "fin_improve", None, None
    if qid == "fin_hardest_other":
        return "final", "fin_hardest", "Другое", None
    if qid == "fin_stay_reason":
        if "Не уверен" in qtext:
            value = "Не уверен"
        elif "уход" in qtext:
            value = "Нет, задумываюсь об уходе"
        else:
            value = "Да, однозначно!"
        return "final", "fin_stay", value, None
    raise ValueError(f"Неизвестный вопрос синтетики: {qid}")


def _option(sv: C.Survey, key: str, value: str | None) -> C.Option | None:
    q = sv.question(key)
    if q is None or value is None:
        return None
    return next((o for o in q.options if o.label == value), None)


def survey_at(code: str, emp) -> datetime:
    """Когда опрос code приходит новичку по сценарию (часовой пояс по умолчанию, как у синтетики)."""
    rule = SURVEY_TIMING[code]
    end = emp.probation_end or (add_months(emp.start_date, 3) - timedelta(days=1))
    d = step_date(rule, SimpleNamespace(start_date=emp.start_date, probation_end=end))
    h, m = (rule.time or "10:00").split(":")
    return datetime.combine(d, time(int(h), int(m)), tzinfo=ZoneInfo(settings.tz_default))


def answer_at(code: str, emp, now: datetime, rnd: random.Random) -> datetime | None:
    """Когда новичок ответил: через 5–90 минут после опроса, не позже now. None — опрос до него ещё не дошёл."""
    sent = survey_at(code, emp)
    if sent > now:
        return None
    return min(sent + timedelta(minutes=rnd.randint(5, 90)), now)


def _priority(row: dict) -> int:
    """Красные сигналы — первыми, потом негатив, потом остальное."""
    if any(h.level == "red" for h in rules.detect(row["text"])):
        return 0
    return 1 if row.get("label") == "negative" else 2


def assign_free(rows: list[dict], by_site: dict[str, list], now: datetime, rnd: random.Random, *,
                skip: set[tuple[int, str]] | frozenset = frozenset()) -> tuple[list[tuple], int]:
    """Свободные ответы Ивана → новичкам их площадки, до которых этот опрос уже дошёл.

    Новичок отвечает на вопрос один раз; нагрузка — поровну. skip — (id новичка, опрос), которые новичок
    не прошёл. → ([(новичок, строка, (опрос, вопрос, вариант, оценка), когда)], сколько строк пропущено).
    """
    targets = [(row, _target(row, row["site"], rnd)) for row in rows]  # rnd — в порядке файла, как раньше
    order = sorted(range(len(rows)), key=lambda i: _priority(rows[i]))
    taken: set[tuple[int, str]] = set()
    load: Counter[int] = Counter()
    placed, skipped = [], 0
    for i in order:
        row, (code, key, value, score) = targets[i]
        hosts = [e for e in by_site.get(row["site"], [])
                 if (e.id, key) not in taken and (e.id, code) not in skip and survey_at(code, e) <= now]
        if not hosts:
            skipped += 1
            continue
        e = min(hosts, key=lambda x: (load[x.id], x.id))
        taken.add((e.id, key))
        load[e.id] += 1
        placed.append((e, row, (code, key, value, score), answer_at(code, e, now, rnd)))
    return placed, skipped


def base_plan(emps: list, now: datetime, rnd: random.Random, *,
              skip: set[tuple[int, str]] | frozenset = frozenset()) -> list[tuple]:
    """Опросы, которые дошли до новичка и на которые он ответил кнопками: [(новичок, опрос, когда)]."""
    out = []
    for e in emps:
        for code in BASE_SURVEYS:
            if (e.id, code) in skip:
                continue
            when = answer_at(code, e, now, rnd)
            if when is not None:
                out.append((e, code, when))
    return out


def _profile(rnd: random.Random, site: str) -> str:
    # производство (GTP) чуть тяжелее офиса — чтобы срез по площадкам был не одинаковым
    weights = {"GTP": (5, 3, 2), "GrPP": (6, 3, 1), "Luzhniki": (7, 2, 1)}.get(site, (6, 3, 1))
    return rnd.choices(["good", "mixed", "bad"], weights=weights)[0]


def _base_answers(sv: C.Survey, profile: str, rnd: random.Random, site: str) -> list[tuple[C.Question, str, int | None]]:
    """Ответы кнопками на весь опрос по «настроению» новичка."""
    out = []
    for q in sv.visible(site):
        if q.kind == "scale":
            n = {"good": rnd.randint(8, 10), "mixed": rnd.randint(6, 9), "bad": rnd.randint(3, 7)}[profile]
            out.append((q, str(n), n))
        elif q.kind in ("choice", "yes_no", "choice_other"):
            opts = [o for o in q.visible_options(site) if o.ask != "other"]
            idx = {"good": 0, "mixed": min(1, len(opts) - 1), "bad": len(opts) - 1}[profile]
            if profile == "mixed" and rnd.random() < 0.5:
                idx = 0
            out.append((q, opts[idx].label, None))
    return out


def _mood(opt: C.Option | None, score: int | None, label: str | None = None) -> str | None:
    if label:
        return MOOD.get(label)
    if score is not None:
        return "positive" if score >= 8 else "neutral" if score >= 5 else "negative"
    return opt.mood if opt else None


async def _reset(s, emps: list[Employee]) -> None:
    synth_ids = select(SurveyAnswer.id).where(SurveyAnswer.is_synthetic)
    ticket_ids = (await s.scalars(select(Flag.ticket_id).where(Flag.answer_id.in_(synth_ids),
                                                               Flag.ticket_id.is_not(None)))).all()
    await s.execute(delete(Flag).where(Flag.answer_id.in_(synth_ids)))
    if ticket_ids:
        await s.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
    await s.execute(delete(SurveyAnswer).where(SurveyAnswer.is_synthetic))
    # «непройденные опросы» прошлого запуска: у синтетики они только от сида, бот им не пишет
    for e in emps:
        if e.dialog_data and ("survey" in e.dialog_data or "surveys" in e.dialog_data):
            e.dialog_data = {k: v for k, v in e.dialog_data.items() if k not in ("survey", "surveys")}
    await s.flush()


def _ticket_status(created: datetime, now: datetime) -> tuple[str, datetime | None, datetime | None]:
    age = now - created
    if age > timedelta(days=10):
        return "closed", created + timedelta(hours=3), created + timedelta(days=2)
    if age > timedelta(days=3):
        return "in_progress", created + timedelta(hours=4), None
    return "new", None, None


async def _stories(s) -> None:
    """Истории риска для «Аналитики» (seed/risks_seed.py): идемпотентно, создаёт только недостающее."""
    stats = await ensure_stories(s)
    await s.commit()
    if stats["answers"]:
        print(f"Истории риска: ответов {stats['answers']}, сигналов {stats['flags']} [синтетика]")


async def seed(reset: bool = False, thin: str | None = None) -> None:
    rnd = random.Random(7)
    now = datetime.now(UTC)
    surveys = C.load()
    rows = [json.loads(line) for line in SYNTH.read_text(encoding="utf-8").splitlines() if line.strip()]

    async with async_session() as s:
        ids = await store.sync(s)
        # отвечают только подключившиеся (active): приглашённые в статусе waiting бота ещё не открыли
        emps = (await s.scalars(select(Employee).where(Employee.is_synthetic, Employee.status == "active")
                                .order_by(Employee.id))).all()
        # у историй риска (seed/risks_seed.py) ответы свои: общая раскладка их не трогает
        emps = [e for e in emps if not is_story(e)]
        if not emps:
            print("Нет синтетических новичков: сначала make seed")
            return
        have = await s.scalar(select(func.count()).select_from(SurveyAnswer).where(SurveyAnswer.is_synthetic,
                                                                                   SurveyAnswer.employee_id.in_(
                                                                                       [e.id for e in emps])))
        if have and not reset:
            print(f"Синтетические ответы уже есть ({have}). Пересоздать: python -m seed.surveys_seed --reset")
            await _stories(s)
            return
        if reset:
            await _reset(s, list(emps))

        by_site: dict[str, list[Employee]] = defaultdict(list)
        for e in emps:
            by_site[e.site_code].append(e)
        if thin in by_site:
            by_site[thin] = by_site[thin][:4]

        answers: dict[tuple[int, int], SurveyAnswer] = {}
        pending_flags: list[tuple[Employee, SurveyAnswer, str, str, str, str]] = []  # emp, ans, level, topic, quote, source

        def put(emp, sv, q, *, value, score, mood, text=None, topic=None, when=None):
            qid = ids[sv.code][q.key]
            ans = answers.get((emp.id, qid))
            if ans is None:
                ans = SurveyAnswer(employee_id=emp.id, question_id=qid, is_synthetic=True)
                answers[(emp.id, qid)] = ans
                s.add(ans)
            ans.value, ans.score, ans.mood = value, score, mood
            ans.topic = topic or q.topic
            if text:
                ans.text = text
            if when is not None:
                ans.created_at = when
            return ans

        # Двое синтетических новичков не прошли чек 2-й недели (шаг 4): их ответов на него нет
        stuck = [x for e_list in by_site.values() for x in e_list if survey_at("week2", x) <= now][:2]
        skip = {(e.id, "week2") for e in stuck}

        # 1. Ответы кнопками на опросы, которые уже дошли до новичка, — в день опроса
        profiles = {e.id: _profile(rnd, e.site_code) for e in emps}
        people = [e for site_people in by_site.values() for e in site_people]
        for e, code, when in base_plan(people, now, rnd, skip=skip):
            sv = surveys[code]
            for q, value, score in _base_answers(sv, profiles[e.id], rnd, e.site_code):
                opt = _option(sv, q.key, value)
                put(e, sv, q, value=value, score=score, mood=_mood(opt, score), when=when)

        # 2. Свободные ответы Ивана — новичкам их площадки, до которых опрос уже дошёл
        texts: list[tuple[Employee, SurveyAnswer, str]] = []
        placed, skipped = assign_free(rows, by_site, now, rnd, skip=skip)
        for e, row, (code, key, value, score), when in placed:
            sv = surveys[code]
            q = sv.question(key)
            topic = TOPIC.get(row["cluster_hint"].split(":")[0].strip(), "другое")
            ans = put(e, sv, q, value=value, score=score, mood=_mood(None, score, row["label"]),
                      text=row["text"], topic=topic, when=when)
            texts.append((e, ans, row["text"]))

        await s.flush()

        # 3. Флаги: кнопки-триггеры и словарь по тексту
        for (emp_id, qid), ans in answers.items():
            e = next(x for x in emps if x.id == emp_id)
            for sv in surveys.values():
                q = next((q for q in sv.questions if ids[sv.code].get(q.key) == qid), None)
                if q is None:
                    continue
                for label in (ans.value or "").split("; "):
                    opt = _option(sv, q.key, label)
                    if opt and opt.flag:
                        quote = f"«{opt.label}». Опрос «{sv.title}», вопрос «{q.text}»."
                        pending_flags.append((e, ans, opt.flag["level"], opt.flag["topic"], quote, "button"))
        for e, ans, text in texts:
            for h in rules.detect(text):
                pending_flags.append((e, ans, h.level, h.topic, text, "survey"))

        n_red = n_yellow = 0
        # красные из одного текста — одно обращение, как у signals.service.raise_hits
        tickets: dict[tuple[int, str], Ticket] = {}
        for e, ans, level, topic, quote, source in pending_flags:
            created = ans.created_at or now
            flag = Flag(employee_id=e.id, level=level, topic=topic, quote=quote[:1000], source=source,
                        answer_id=ans.id, created_at=created, status="new")
            if level == "red":
                status, first, closed = _ticket_status(created, now)
                ticket = tickets.get((ans.id, quote))
                if ticket is None:
                    ticket = Ticket(employee_id=e.id, source="flag", category=TICKET_CATEGORY.get(topic, topic),
                                    text=quote[:1000], status=status, assignee_id=e.recruiter_id,
                                    created_at=created, first_response_at=first, closed_at=closed)
                    s.add(ticket)
                    await s.flush()
                    tickets[(ans.id, quote)] = ticket
                flag.ticket_id = ticket.id
                flag.status = "resolved" if status == "closed" else "new"
                n_red += 1
            else:
                flag.status = "resolved" if now - created > timedelta(days=14) else "new"
                n_yellow += 1
            s.add(flag)

        # 4. Двое синтетических новичков не прошли чек 2-й недели — для «непройденных опросов» в Mini App
        for e in stuck:
            started = survey_at("week2", e)
            data = dict(e.dialog_data or {})
            data["survey"] = {"code": "week2", "q": 3, "mid": None, "started_at": started.isoformat(),
                              "reminded": 2, "done": False, "sel": [], "answer_id": None, "wait": None}
            data["surveys"] = {**(data.get("surveys") or {}), "week2": {"started_at": started.isoformat()}}
            e.dialog_data = data

        await s.commit()
        await _stories(s)  # --reset удалил и ответы историй риска: вернуть

        # Итог: ответившие за 4 недели по площадкам (порог сводки — 5 разных новичков)
        since = now - timedelta(days=28)
        rows_by_site = (await s.execute(
            select(Employee.site_code, func.count(func.distinct(SurveyAnswer.employee_id)), func.count())
            .join(Employee, Employee.id == SurveyAnswer.employee_id)
            .where(SurveyAnswer.is_synthetic, SurveyAnswer.created_at >= since)
            .group_by(Employee.site_code))).all()
        total = await s.scalar(select(func.count()).select_from(SurveyAnswer).where(SurveyAnswer.is_synthetic))
    print(f"Синтетические ответы: {total} (из них {len(texts)} свободных, ещё {skipped} не к кому положить), "
          f"флаги: красных {n_red}, жёлтых {n_yellow} [синтетика]")
    for site, people, n in sorted(rows_by_site):
        mark = "" if people >= 5 else "  ← мало данных"
        print(f"  {site:<9} за 4 недели: {people} новичков, {n} ответов{mark}")
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--thin", default=None)
    args = ap.parse_args()
    asyncio.run(seed(reset=args.reset, thin=args.thin))
