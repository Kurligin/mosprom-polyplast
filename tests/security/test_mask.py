"""Маскирование перед LLM: ФИО всех сотрудников с падежами, телефоны, e-mail, номера документов.

Все имена и номера — синтетические.
"""

from datetime import date, timedelta

import pytest

from app.db.models import Employee
from app.privacy import mask, mask_with_classes
from app.privacy import names as N

STAFF = [
    "Петров Сергей Викторович",
    "Смирнова Ольга",
    "Вишневский Павел Андреевич",
    "Начальников Пётр Андреевич (руководитель, демо)",
    "Тестова Анна Сергеевна (HR, демо)",
]


@pytest.mark.parametrize(("text", "gone"), [
    ("Руководитель Петров Сергей Викторович на меня постоянно кричит при всех", ["Петров", "Сергей", "Викторович"]),
    ("наставник Ольга Смирнова не отвечает", ["Ольга", "Смирнова"]),
    ("передал Петрову, но Сергею Викторовичу некогда", ["Петров", "Серге", "Викторович"]),
    ("с Петровым и Смирновой поговорил", ["Петров", "Смирнов"]),
    ("у Вишневского спросил, Вишневская тоже не знает", ["Вишневск"]),
    ("попросил Павла, потом Петра", ["Павл", "Петр"]),
    ("Пётр Андреевич сказал ждать", ["Пётр", "Андреевич"]),
    ("Анне Сергеевне написал", ["Анне", "Сергеевне"]),
])
def test_staff_names_with_cases(text, gone):
    out, classes = mask_with_classes(text, STAFF)
    for g in gone:
        assert g.lower() not in out.lower(), (g, out)
    assert "[ФИО]" in out and "name" in classes


def test_notes_and_roles_are_not_names():
    """Пометки в скобках и роли из карточек — не имена: «руководитель», «HR», «демо» остаются."""
    text = "руководитель и HR из демо, начальник смены, новичок"
    assert mask(text, STAFF + ["Тестовый Новичок GTP"]) == text


def test_common_words_not_masked():
    text = "главное, что наставник объяснил, верно и подробно"
    assert mask(text, ["Вера Главнова", "Роман Иванов"]) == text


@pytest.mark.parametrize("phone", [
    "+7 916 123 45 67", "+7 (916) 123-45-67", "8 916 123-45-67", "89161234567", "79161234567",
    "916 123-45-67", "9161234567", "(916) 123 45 67", "(495) 745-68-57", "+7 (495) 745-68-57",
    "495 745 68 57", "745-68-57", "8-916-123-45-67",
])
def test_phones_any_format(phone):
    out, classes = mask_with_classes(f"мой номер {phone}, звони", [])
    assert out == "мой номер [ТЕЛЕФОН], звони", out
    assert "phone" in classes


@pytest.mark.parametrize("doc", [
    "4508 123456", "45 08 123456", "4508123456", "123-456-789 01", "12345678901",
    "770123456789", "7701234567", "4276 1234 5678 9012", "4276123456789012",
])
def test_document_numbers(doc):
    out, classes = mask_with_classes(f"данные: {doc}.", [])
    assert not any(ch.isdigit() for ch in out), out
    assert "document" in classes


def test_passport_with_words():
    out = mask("паспорт серия 45 08 номер 123456, СНИЛС 123-456-789 01, ИНН 770123456789", [])
    assert not any(ch.isdigit() for ch in out), out


def test_email():
    out, classes = mask_with_classes("пиши на o.smirnova@poly-plast.ru или a_b+1@mail.example.com", [])
    assert "@" not in out and out.count("[EMAIL]") == 2 and "email" in classes


@pytest.mark.parametrize("text", [
    "Обед с 13:00 до 13:48",
    "пункт 6.5.2 ПВТР",
    "отпуск 28 календарных дней",
    "смена 2024-10-23 с 8 до 20",
    "премия 1 000 000 рублей",
    "в цеху 3 линии и 120 человек",
    "день 60, встреча 30",
])
def test_regular_numbers_stay(text):
    assert mask(text, STAFF) == text


@pytest.mark.asyncio
async def test_known_names_from_db(session, world):
    """Имена всех сотрудников из БД: персонал и живые новички, без пометок; синтетика — не люди."""
    start = date.today()
    live = Employee(full_name="Кузнецов Игорь Олегович", first_name="Игорь", site_code=world.site.code,
                    work_type="production", start_date=start, probation_end=start + timedelta(days=90),
                    status="active")
    session.add(live)
    await session.flush()
    names = await N.load_names(session)
    assert "Кузнецов Игорь Олегович" in names
    assert "Начальников Пётр Андреевич" in names           # руководитель, без «(руководитель, синтетика)»
    assert not any("синтетика" in n for n in names)
    assert not any(n.startswith("Синтетиков") for n in names)  # синтетический новичок — не человек
    out = mask("Игорь Олегович и Начальникову Петру передал", names)
    assert "Игор" not in out and "Олегович" not in out and "Начальников" not in out and "Петр" not in out


@pytest.mark.asyncio
async def test_known_names_cached(monkeypatch):
    calls = []

    async def fake_load(session=None):
        calls.append(1)
        return ["Петров Сергей"]

    monkeypatch.setattr(N, "_load", fake_load)
    N.invalidate()
    assert await N.known_names() == ["Петров Сергей"]
    assert await N.known_names() == ["Петров Сергей"]
    assert len(calls) == 1  # второй раз — из кэша
    N.invalidate()
    await N.known_names()
    assert len(calls) == 2
    N.invalidate()


@pytest.mark.asyncio
async def test_known_names_db_down_keeps_last(monkeypatch):
    async def ok(session=None):
        return ["Петров Сергей"]

    async def down(session=None):
        raise OSError("db down")

    N.invalidate()
    monkeypatch.setattr(N, "_load", ok)
    await N.known_names()
    monkeypatch.setattr(N, "_cache", {**N._cache, "at": 0.0})  # кэш устарел
    monkeypatch.setattr(N, "_load", down)
    assert await N.known_names() == ["Петров Сергей"]
    N.invalidate()
