"""Демо-данные (все синтетические). Запуск: python -m seed.seed [--links]

Идемпотентно: существующие записи и их коды не меняются.
У синтетических новичков прошедшие встречи с руководителем отмечаются проведёнными,
кроме двух самых свежих: на экране руководителя они «пора встретиться».
Истории риска для экрана «Аналитика» и согласия синтетических новичков — seed/risks_seed.py.
--links — только напечатать ссылки привязки, ничего не создавая.
"""

import asyncio
import random
import sys
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db.models import Employee, Site, Staff, new_code
from app.db.session import async_session, engine
from seed.risks_seed import ensure_stories, is_story

# Площадки — теги из docs/korsik-scenario.md; названия условные до ответа экспертов
SITES = [
    ("GrPP", "Группа ПОЛИПЛАСТИК (GrPP)"),
    ("Luzhniki", "Офис «Лужники»"),
    ("GTP", "Площадка GTP"),
]

# Персонал (вымышленные ФИО)
# Обычные фамилии, а не слова: ФИО персонала маскируются перед LLM, и фамилия-слово («Подборова»)
# прятала бы в текстах обычные слова («отдел подбора»)
STAFF = [
    ("Соколова Анна Сергеевна (HR, демо)", "hr", None),
    ("Морозова Мария Игоревна (рекрутер, демо)", "recruiter", None),
    ("Белов Пётр Андреевич (руководитель, демо)", "manager", "GTP"),
    ("Лебедева Ольга Викторовна (руководитель HR, демо)", "head_recruitment", None),
]
# Прежние ФИО персонала → новые: сид переименовывает, а не заводит второго человека
RENAMED_STAFF = {
    "Тестова Анна Сергеевна (HR, демо)": "Соколова Анна Сергеевна (HR, демо)",
    "Подборова Мария Игоревна (рекрутер, демо)": "Морозова Мария Игоревна (рекрутер, демо)",
    "Начальников Пётр Андреевич (руководитель, демо)": "Белов Пётр Андреевич (руководитель, демо)",
    "Главная Ольга Викторовна (глава подбора, демо)": "Лебедева Ольга Викторовна (руководитель HR, демо)",
    "Главная Ольга Викторовна (руководитель HR, демо)": "Лебедева Ольга Викторовна (руководитель HR, демо)",
}

# Тестовые новички для команды: (ФИО, имя, площадка, офис/производство)
TEST_EMPLOYEES = [
    ("Тестовый Новичок GTP", "Тест", "GTP", "production"),
    ("Тестовый Новичок Лужники", "Тест", "Luzhniki", "office"),
]

# Явно вымышленные ФИО для синтетики
SYNTH_FIRST = ["Демьян", "Синтия", "Пробор", "Вымысла", "Макет", "Образца", "Шаблон", "Примерна",
               "Тестислав", "Условна", "Модель", "Эскиза"]
SYNTH_LAST = ["Синтетиков", "Демонстрова", "Примеров", "Вымышленная", "Макетов", "Образцова",
              "Шаблонов", "Условная", "Пробников", "Эскизова", "Тестерова", "Моделин"]

# Синтетические новички, которые ещё не открыли ссылку из оффера («Не подключился» в Mini App):
# (фамилия, имя, площадка, офис/производство, через сколько дней выход — от даты первого запуска сида)
WAITING = [
    ("Образцова", "Синтия", "Luzhniki", "office", 2),
    ("Макетов", "Шаблон", "GTP", "production", 4),
]

# Синтетические новички дальше по испытательному сроку: у них уже были опросы двух месяцев и итоговый.
# (фамилия, имя, площадка, офис/производство, сколько дней назад вышел — от даты первого запуска сида)
# Около 53 и 93 дней: опрос двух месяцев и итоговый пришли пару дней назад, в очереди есть свежие сигналы.
VETERANS = [
    ("Пробников", "Тестислав", "GTP", "production", 53),
    ("Моделин", "Эскиза", "GTP", "production", 72),
    ("Шаблонов", "Пробор", "GTP", "office", 93),
    ("Тестерова", "Вымысла", "GTP", "production", 99),
    ("Условная", "Образца", "GrPP", "office", 61),
    ("Эскизова", "Примерна", "GrPP", "office", 94),
    ("Демонстрова", "Модель", "GrPP", "production", 100),
    ("Вымышленная", "Условна", "Luzhniki", "office", 64),
    ("Макетов", "Демьян", "Luzhniki", "office", 96),
]
KEEP_OVERDUE = 2  # встречи «пора встретиться» для демо экрана руководителя


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    # последний день месяца, если число не помещается
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


async def seed() -> None:
    rnd = random.Random(42)  # детерминированный набор синтетики
    today = date.today()

    async with async_session() as s:
        # Площадки
        for code, name in SITES:
            if await s.get(Site, code) is None:
                s.add(Site(code=code, name=name, tz=settings.tz_default))
        await s.flush()

        # Персонал: ищем по ФИО+роли, код не трогаем
        staff_by_role: dict[str, Staff] = {}
        for old, new in RENAMED_STAFF.items():
            st = await s.scalar(select(Staff).where(Staff.full_name == old))
            if st is not None and await s.scalar(select(Staff.id).where(Staff.full_name == new)) is None:
                st.full_name = new
        await s.flush()
        for full_name, role, site_code in STAFF:
            st = await s.scalar(select(Staff).where(Staff.full_name == full_name, Staff.role == role))
            if st is None:
                st = Staff(full_name=full_name, role=role, site_code=site_code, bind_code=new_code())
                s.add(st)
            staff_by_role[role] = st
        await s.flush()
        manager, recruiter = staff_by_role["manager"], staff_by_role["recruiter"]

        # Тестовые новички: выход через 2 дня, испытательный 3 месяца
        for full_name, first_name, site_code, work_type in TEST_EMPLOYEES:
            exists = await s.scalar(select(Employee).where(Employee.full_name == full_name, Employee.is_test))
            if exists is None:
                start = today + timedelta(days=2)
                s.add(Employee(
                    full_name=full_name, first_name=first_name, site_code=site_code, work_type=work_type,
                    start_date=start, probation_end=add_months(start, 3),
                    manager_id=manager.id, recruiter_id=recruiter.id,
                    invite_code=new_code(), is_test=True,
                ))

        # Синтетические новички: 7 на площадку, выход за последние 6 недель
        for code, _ in SITES:
            have = await s.scalar(
                select(Employee.id).where(Employee.site_code == code, Employee.is_synthetic).limit(1)
            )
            if have is not None:
                continue
            for i in range(7):
                first = rnd.choice(SYNTH_FIRST)
                last = rnd.choice(SYNTH_LAST)
                start = today - timedelta(days=rnd.randint(0, 42))
                s.add(Employee(
                    full_name=f"{last} {first} [синтетика]", first_name=first, site_code=code,
                    work_type=rnd.choice(["office", "production"]),
                    start_date=start, probation_end=add_months(start, 3),
                    manager_id=manager.id, recruiter_id=recruiter.id,
                    invite_code=new_code(), status="active", address_form=rnd.choice(["ty", "vy"]),
                    is_synthetic=True,
                ))
        await s.flush()

        # Пригласили, но к боту ещё не подключились: статус waiting, ответов и вопросов нет
        for last, first, code, work_type, in_days in WAITING:
            full_name = f"{last} {first} [синтетика]"
            if await s.scalar(select(Employee.id).where(Employee.full_name == full_name, Employee.is_synthetic)):
                continue
            start = today + timedelta(days=in_days)
            s.add(Employee(
                full_name=full_name, first_name=first, site_code=code, work_type=work_type,
                start_date=start, probation_end=add_months(start, 3),
                manager_id=manager.id, recruiter_id=recruiter.id,
                invite_code=new_code(), status="waiting", address_form="ty", is_synthetic=True,
            ))

        # Дальше по испытательному сроку: подключились, бот ведёт их давно
        for last, first, code, work_type, ago in VETERANS:
            full_name = f"{last} {first} [синтетика]"
            if await s.scalar(select(Employee.id).where(Employee.full_name == full_name, Employee.is_synthetic)):
                continue
            start = today - timedelta(days=ago)
            s.add(Employee(
                full_name=full_name, first_name=first, site_code=code, work_type=work_type,
                start_date=start, probation_end=add_months(start, 3),
                manager_id=manager.id, recruiter_id=recruiter.id,
                invite_code=new_code(), status="active", address_form=rnd.choice(["ty", "vy"]),
                is_synthetic=True,
            ))
        await s.flush()

        synth = (await s.scalars(select(Employee).where(Employee.is_synthetic, Employee.status == "active",
                                                        Employee.manager_id.is_not(None))
                                 .order_by(Employee.id))).all()
        # у историй риска встречи свои (seed/risks_seed.py): просроченная встреча — часть истории
        done, kept = await settle_meetings(s, [e for e in synth if not is_story(e)], today)
        stories = await ensure_stories(s)
        await s.commit()

    print(f"Встречи синтетических новичков: отмечено проведёнными {done}, ждут отметки {kept} [синтетика]")
    print(f"Истории риска для «Аналитики»: {stories['stories']}, согласия у {stories['consents']} новичков "
          "[синтетика]")
    await print_links()


async def settle_meetings(session, emps: list[Employee], today: date,
                          keep_overdue: int = KEEP_OVERDUE) -> tuple[int, int]:
    """Синтетика: прошедшие встречи с руководителем проведены, кроме keep_overdue самых свежих (по одной у новичка).

    Оставленные — демо «пора встретиться» на экране руководителя. Встречи создаёт `manager.service.ensure_tasks`,
    отправленные push и отметки руководителя не трогаем. → (сколько отметили сейчас, сколько ждут отметки).
    Коммит — за вызывающим.
    """
    from app.manager.service import ensure_tasks

    past: dict[int, list] = {}
    for e in emps:
        if not e.is_synthetic or not e.manager_id:
            continue
        tasks = [t for t in await ensure_tasks(session, e)
                 if t.manager_id == e.manager_id and t.due_date and t.due_date < today]
        if tasks:
            past[e.id] = sorted(tasks, key=lambda t: t.due_date)
    # кто ждёт отметки: самые свежие прошедшие встречи; если отметки уже ждут — те же новички
    waiting = [ts[-1] for ts in past.values() if ts[-1].status == "pending"]
    waiting.sort(key=lambda t: (t.due_date, t.id), reverse=True)
    keep = {t.id for t in waiting[:keep_overdue]}
    tz = ZoneInfo(settings.tz_default)
    done = 0
    for tasks in past.values():
        for t in tasks:
            if t.id in keep or t.status != "pending" or t.mid:
                continue
            t.status = "done"
            t.done_at = datetime.combine(t.due_date, time(15, 0), tzinfo=tz).astimezone(UTC)
            done += 1
    await session.flush()
    return done, len(keep)


async def print_links() -> None:
    bot = settings.max_bot_name or "<MAX_BOT_NAME>"
    base = f"https://max.ru/{bot}?start="
    async with async_session() as s:
        staff = (await s.scalars(select(Staff).order_by(Staff.id))).all()
        tests = (await s.scalars(select(Employee).where(Employee.is_test).order_by(Employee.id))).all()
        n_synth = await s.scalar(select(Employee.id).where(Employee.is_synthetic).limit(1))

    rows = [(f"новичок {e.site_code}", e.full_name, base + e.invite_code) for e in tests]
    rows += [(st.role, st.full_name, base + st.bind_code) for st in staff]
    w1 = max(len(r[0]) for r in rows)
    w2 = max(len(r[1]) for r in rows)
    print("\nСсылки привязки (данные синтетические):")
    for role, name, link in rows:
        print(f"  {role:<{w1}}  {name:<{w2}}  {link}")
    if n_synth is None:
        print("  (синтетических новичков нет — запустите make seed)")
    print()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(print_links() if "--links" in sys.argv else seed())
