"""Демо-данные (все синтетические). Запуск: python -m seed.seed [--links]

Идемпотентно: существующие записи и их коды не меняются.
--links — только напечатать ссылки привязки, ничего не создавая.
"""

import asyncio
import random
import sys
from datetime import date, timedelta

from sqlalchemy import select

from app.config import settings
from app.db.models import Employee, Site, Staff, new_code
from app.db.session import async_session, engine

# Площадки — теги из docs/korsik-scenario.md; названия условные до ответа экспертов
SITES = [
    ("GrPP", "Группа ПОЛИПЛАСТИК (GrPP)"),
    ("Luzhniki", "Офис «Лужники»"),
    ("GTP", "Площадка GTP"),
]

# Персонал (вымышленные ФИО)
STAFF = [
    ("Тестова Анна Сергеевна (HR, демо)", "hr", None),
    ("Подборова Мария Игоревна (рекрутер, демо)", "recruiter", None),
    ("Начальников Пётр Андреевич (руководитель, демо)", "manager", "GTP"),
    ("Главная Ольга Викторовна (глава подбора, демо)", "head_recruitment", None),
]

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

        await s.commit()

    await print_links()


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
