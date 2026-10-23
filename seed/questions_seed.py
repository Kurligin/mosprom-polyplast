"""Синтетические вопросы новичков боту → questions_log (для «Частых тем» в Mini App и тем сводки).

Запуск: python -m seed.questions_seed [--reset]
  --reset  удалить прежние вопросы синтетических новичков и создать заново.

Тексты — эталонные вопросы eval/golden.jsonl. Задают их синтетические новички в статусе active
(сначала make seed), даты — за последние 6 недель, в типичные для темы дни адаптации:
документы и дорога — до выхода, доступы и обед — в первые дни, зарплата — к первой выплате, отпуск — позже.
Маршрут — как у бота: answerable → answer, out_of_kb → unknown, escalate → personal.
Обращения и сообщения не создаются, в «МАКС» ничего не уходит. Идемпотентно: если вопросы
синтетических новичков уже есть, ничего не делаем.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app.config import settings
from app.db.models import Employee, QuestionLog
from app.db.session import async_session, engine
from seed.risks_seed import is_story

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "golden.jsonl"
WINDOW_DAYS = 42
ROUTES = {"answerable": "answer", "out_of_kb": "unknown", "escalate": "personal"}

# Тема вопроса: словарь тем бота (config/synonyms.yaml, app.kb.query.topic_of) там, где он есть.
# Личные вопросы (escalate) — без темы: в Mini App это строка «Личные вопросы».
TOPICS = {
    "g001": "рабочий день", "g002": "обед", "g003": "зарплата", "g004": "зарплата", "g005": "отпуск",
    "g006": "отпуск", "g007": "отпуск", "g008": "отпуск", "g009": "выходные", "g010": "больничный",
    "g011": "больничный", "g012": "испытательный срок", "g013": "испытательный срок",
    "g014": "документы при приеме", "g015": "документы при приеме", "g016": "документы при приеме",
    "g017": "документы при приеме", "g018": "документы при приеме", "g019": "рабочий день",
    "g020": "выходные", "g021": "обед", "g022": "дресс-код", "g023": "коммерческая тайна",
    "g024": "рабочий день", "g025": "подарки и коррупция", "g026": "подарки и коррупция",
    "g027": "подарки и коррупция", "g028": "подарки и коррупция", "g029": "подарки и коррупция",
    "g030": "подарки и коррупция", "g031": "коммерческая тайна", "g032": "коммерческая тайна",
    "g033": "коммерческая тайна", "g034": "коммерческая тайна", "g035": "коммерческая тайна",
    "g036": "коммерческая тайна", "g037": "справочник", "g038": "справочник", "g039": "доступы",
    "g040": "о компании", "g041": "о компании", "g042": "спортзал", "g043": "больничный",
    "g044": "увольнение", "g045": "увольнение", "g046": "транспорт", "g047": "транспорт", "g048": "обед",
    "g049": "выходные", "g050": "документы при приеме",
    "g066": "ДМС", "g067": "пропуск", "g068": "доступы", "g069": "командировка",
    "g070": "справки", "g071": "спортзал", "g072": "спецодежда", "g073": "корпоративы",
}

# Тема → (сколько вопросов за 6 недель, дни адаптации «с», «по»; день 1 — дата выхода)
PROFILE = {
    "зарплата": (22, 9, 20),
    "доступы": (20, 1, 4),
    "документы при приеме": (18, -2, 1),
    "отпуск": (15, 14, 42),
    "обед": (14, 1, 3),
    "транспорт": (12, -2, 2),
    "рабочий день": (11, 1, 6),
    None: (10, 5, 42),  # личные вопросы
    "справочник": (9, 1, 5),
    "больничный": (9, 5, 40),
    "коммерческая тайна": (8, 3, 12),
    "испытательный срок": (7, 20, 42),
    "подарки и коррупция": (6, 4, 15),
    "выходные": (6, 10, 40),
    "ДМС": (5, 7, 21),
    "о компании": (4, 1, 10),
    "спортзал": (4, 3, 14),
    "пропуск": (4, 1, 10),
    "дресс-код": (3, -2, 2),
    "корпоративы": (3, 7, 30),
    "спецодежда": (3, 1, 5),
    "командировка": (2, 14, 42),
    "справки": (2, 10, 30),
    "увольнение": (2, 20, 42),
}


def plan_rows(golden: list[dict], emps: list[Employee], today: date, now: datetime,
              rnd: random.Random) -> list[dict]:
    """Вопросы для questions_log: [{employee_id, question, route, topic, created_at}]."""
    tz = ZoneInfo(settings.tz_default)
    lo = today - timedelta(days=WINDOW_DAYS)
    by_topic: dict[str | None, list[dict]] = defaultdict(list)
    for g in golden:
        route = ROUTES.get(g.get("category"), "answer")
        topic = None if route == "personal" else TOPICS.get(g["id"])
        if route != "personal" and topic is None:
            continue
        by_topic[topic].append({"question": g["question"], "route": route, "site": g.get("site") or "any"})

    out = []
    for topic, items in by_topic.items():
        total, d_from, d_to = PROFILE.get(topic, (2, 1, 42))
        for i in range(total):
            item = items[i % len(items)]
            people = [e for e in emps if item["site"] in ("any", e.site_code)] or emps
            # кто мог спросить: нужный день адаптации уже наступил и попадает в окно 6 недель
            choices = []
            for e in people:
                days = [d for d in range(d_from, d_to + 1) if lo <= e.start_date + timedelta(days=d - 1) <= today]
                if days:
                    choices.append((e, days))
            if not choices:
                continue
            e, days = rnd.choice(choices)
            # пик в начале окна темы: ранние дни вероятнее
            d = days[min(int(rnd.triangular(0, len(days), 0)), len(days) - 1)]
            when = datetime.combine(e.start_date + timedelta(days=d - 1),
                                    time(rnd.randint(8, 19), rnd.randint(0, 59)), tzinfo=tz).astimezone(UTC)
            if when > now:
                when = now - timedelta(minutes=rnd.randint(5, 180))
            out.append({"employee_id": e.id, "question": item["question"], "route": item["route"],
                        "topic": topic, "created_at": when})
    out.sort(key=lambda r: r["created_at"])
    return out


async def seed(reset: bool = False) -> None:
    rnd = random.Random(11)
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo(settings.tz_default)).date()
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]

    async with async_session() as s:
        emps = (await s.scalars(select(Employee).where(Employee.is_synthetic, Employee.status == "active")
                                .order_by(Employee.id))).all()
        # у историй риска (seed/risks_seed.py) вопросы свои: общая раскладка их не трогает
        emps = [e for e in emps if not is_story(e)]
        if not emps:
            print("Нет синтетических новичков: сначала make seed")
            return
        stories = [e.id for e in await s.scalars(select(Employee).where(Employee.is_synthetic)) if is_story(e)]
        synth_ids = select(Employee.id).where(Employee.is_synthetic, Employee.id.not_in(stories))
        have = await s.scalar(select(func.count()).select_from(QuestionLog)
                              .where(QuestionLog.employee_id.in_(synth_ids)))
        if have and not reset:
            print(f"Вопросы синтетических новичков уже есть ({have}). "
                  "Пересоздать: python -m seed.questions_seed --reset")
            await engine.dispose()
            return
        if reset:
            await s.execute(delete(QuestionLog).where(QuestionLog.employee_id.in_(synth_ids)))

        rows = plan_rows(golden, list(emps), today, now, rnd)
        s.add_all(QuestionLog(**r) for r in rows)
        await s.commit()

    routes = Counter(r["route"] for r in rows)
    topics = Counter(r["topic"] or "личные вопросы" for r in rows)
    print(f"Вопросы синтетических новичков: {len(rows)} (ответ {routes['answer']}, нет в базе {routes['unknown']}, "
          f"личные {routes['personal']}) [синтетика]")
    print("  Частые темы: " + ", ".join(f"{t} {n}" for t, n in topics.most_common(6)))
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reset", action="store_true")
    asyncio.run(seed(reset=ap.parse_args().reset))
