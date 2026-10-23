"""Синтетические ответы опросов, флаги и обращения из них (все данные синтетические).

Запуск: python -m seed.surveys_seed [--reset] [--thin SITE]
  --reset      удалить прежние синтетические ответы, их флаги и обращения и создать заново;
  --thin SITE  у площадки SITE оставить 4 ответивших за 4 недели (показать «мало данных» в сводке).

Источник текстов — 120 синтетических ответов Ивана (ветка ideas/ivan, docs/ideas/07-survey-answers-synth.jsonl,
копия — eval/flags/survey-synth.jsonl). Ответы раскладываются по синтетическим новичкам их площадки
(сначала make seed), даты — за последние 6 недель. К каждому новичку добавляются ответы кнопками
(опрос 3-го дня, чек 2-й недели, адаптационный план), чтобы у сводки были оценки и порог «5 разных новичков».
Флаги — словарём app/signals/rules.py и кнопками-триггерами из config/surveys.yaml.
В HR-группу ничего не отправляется.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from app.db.models import Employee, Flag, SurveyAnswer, Ticket
from app.db.session import async_session, engine
from app.signals import rules
from app.signals.service import TICKET_CATEGORY
from app.surveys import config as C
from app.surveys import store

SYNTH = Path(__file__).resolve().parents[1] / "eval" / "flags" / "survey-synth.jsonl"
WINDOW_DAYS = 42
MOOD = {"negative": "negative", "neutral": "neutral", "positive": "positive"}

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


def _when(rnd: random.Random, start: date, today: date, *, day: int | None = None) -> datetime | None:
    """Дата ответа: день адаптации day (если уже наступил) или случайный день в окне 6 недель."""
    lo = max(start, today - timedelta(days=WINDOW_DAYS))
    if day is not None:
        d = start + timedelta(days=day - 1)
        if d > today or d < lo:
            return None
    else:
        span = (today - lo).days
        d = lo + timedelta(days=rnd.randint(0, max(span, 0)))
    return datetime.combine(d, time(rnd.randint(6, 15), rnd.randint(0, 59)), tzinfo=UTC)


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


async def _reset(s) -> None:
    synth_ids = select(SurveyAnswer.id).where(SurveyAnswer.is_synthetic)
    ticket_ids = (await s.scalars(select(Flag.ticket_id).where(Flag.answer_id.in_(synth_ids),
                                                               Flag.ticket_id.is_not(None)))).all()
    await s.execute(delete(Flag).where(Flag.answer_id.in_(synth_ids)))
    if ticket_ids:
        await s.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
    await s.execute(delete(SurveyAnswer).where(SurveyAnswer.is_synthetic))
    await s.flush()


def _ticket_status(created: datetime, now: datetime) -> tuple[str, datetime | None, datetime | None]:
    age = now - created
    if age > timedelta(days=10):
        return "closed", created + timedelta(hours=3), created + timedelta(days=2)
    if age > timedelta(days=3):
        return "in_progress", created + timedelta(hours=4), None
    return "new", None, None


async def seed(reset: bool = False, thin: str | None = None) -> None:
    rnd = random.Random(7)
    today = date.today()
    now = datetime.now(UTC)
    surveys = C.load()
    rows = [json.loads(line) for line in SYNTH.read_text(encoding="utf-8").splitlines() if line.strip()]

    async with async_session() as s:
        ids = await store.sync(s)
        emps = (await s.scalars(select(Employee).where(Employee.is_synthetic).order_by(Employee.id))).all()
        if not emps:
            print("Нет синтетических новичков: сначала make seed")
            return
        have = await s.scalar(select(func.count()).select_from(SurveyAnswer).where(SurveyAnswer.is_synthetic))
        if have and not reset:
            print(f"Синтетические ответы уже есть ({have}). Пересоздать: python -m seed.surveys_seed --reset")
            return
        if reset:
            await _reset(s)

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

        # 1. Ответы кнопками: 3-й день, 2-я неделя, адаптационный план — по дню адаптации
        profiles = {e.id: _profile(rnd, e.site_code) for e in emps}
        for site, people in by_site.items():
            for e in people:
                for code, day in (("day3", 3), ("week2", 14), ("plan", 32)):
                    when = _when(rnd, e.start_date, today, day=day)
                    if when is None:
                        continue
                    sv = surveys[code]
                    for q, value, score in _base_answers(sv, profiles[e.id], rnd, site):
                        opt = _option(sv, q.key, value)
                        put(e, sv, q, value=value, score=score, mood=_mood(opt, score), when=when)

        # 2. Свободные ответы Ивана — по новичкам их площадки, по кругу
        turn: dict[str, int] = defaultdict(int)
        texts: list[tuple[Employee, SurveyAnswer, str]] = []
        for row in rows:
            people = by_site.get(row["site"]) or emps
            e = people[turn[row["site"]] % len(people)]
            turn[row["site"]] += 1
            code, key, value, score = _target(row, e.site_code, rnd)
            sv = surveys[code]
            q = sv.question(key)
            topic = TOPIC.get(row["cluster_hint"].split(":")[0].strip(), "другое")
            ans = put(e, sv, q, value=value, score=score, mood=_mood(None, score, row["label"]),
                      text=row["text"], topic=topic, when=_when(rnd, e.start_date, today))
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
        for e, ans, level, topic, quote, source in pending_flags:
            created = ans.created_at or now
            flag = Flag(employee_id=e.id, level=level, topic=topic, quote=quote[:1000], source=source,
                        answer_id=ans.id, created_at=created, status="new")
            if level == "red":
                status, first, closed = _ticket_status(created, now)
                ticket = Ticket(employee_id=e.id, source="flag", category=TICKET_CATEGORY.get(topic, topic),
                                text=quote[:1000], status=status, assignee_id=e.recruiter_id, created_at=created,
                                first_response_at=first, closed_at=closed)
                s.add(ticket)
                await s.flush()
                flag.ticket_id = ticket.id
                flag.status = "resolved" if status == "closed" else "new"
                n_red += 1
            else:
                flag.status = "resolved" if now - created > timedelta(days=14) else "new"
                n_yellow += 1
            s.add(flag)

        # 4. Двое синтетических новичков не прошли чек 2-й недели — для «непройденных опросов» в Mini App
        for e in [x for x in emps if (today - x.start_date).days >= 14][:2]:
            started = datetime.combine(e.start_date + timedelta(days=14), time(7, 0), tzinfo=UTC)
            data = dict(e.dialog_data or {})
            data["survey"] = {"code": "week2", "q": 3, "mid": None, "started_at": started.isoformat(),
                              "reminded": 2, "done": False, "sel": [], "answer_id": None, "wait": None}
            data["surveys"] = {**(data.get("surveys") or {}), "week2": {"started_at": started.isoformat()}}
            e.dialog_data = data

        await s.commit()

        # Итог: ответившие за 4 недели по площадкам (порог сводки — 5 разных новичков)
        since = now - timedelta(days=28)
        rows_by_site = (await s.execute(
            select(Employee.site_code, func.count(func.distinct(SurveyAnswer.employee_id)), func.count())
            .join(Employee, Employee.id == SurveyAnswer.employee_id)
            .where(SurveyAnswer.is_synthetic, SurveyAnswer.created_at >= since)
            .group_by(Employee.site_code))).all()
        total = await s.scalar(select(func.count()).select_from(SurveyAnswer).where(SurveyAnswer.is_synthetic))
    print(f"Синтетические ответы: {total} (из них {len(texts)} свободных), флаги: красных {n_red}, "
          f"жёлтых {n_yellow} [синтетика]")
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
