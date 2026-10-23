"""Производственный календарь РФ 2026–2027."""
from datetime import date, timedelta

from app.scenario.workcal import is_workday, next_workday, nth_workday


def _count(year):
    d, n = date(year, 1, 1), 0
    while d.year == year:
        n += is_workday(d)
        d += timedelta(days=1)
    return n


def test_new_year_2026():
    # 1–11 января 2026 — нерабочие (перенос 3 янв → 9 янв), выход 12 января
    for day in range(1, 12):
        assert not is_workday(date(2026, 1, day)), day
    assert is_workday(date(2026, 1, 12))


def test_holidays_2026():
    assert not is_workday(date(2026, 5, 9))   # суббота, День Победы
    assert not is_workday(date(2026, 5, 11))  # перенос с 9 мая
    assert not is_workday(date(2026, 3, 9))   # перенос с 8 марта (вс)
    assert not is_workday(date(2026, 11, 4))
    assert not is_workday(date(2026, 12, 31))  # перенос с 4 января
    assert is_workday(date(2026, 12, 30))


def test_2027_transfers():
    assert is_workday(date(2027, 2, 20))      # рабочая суббота
    assert not is_workday(date(2027, 2, 22))  # перенос с 20 февраля
    assert not is_workday(date(2027, 11, 5))  # перенос со 2 января
    assert not is_workday(date(2027, 1, 8))
    assert is_workday(date(2027, 1, 11))


def test_workdays_total():
    assert _count(2026) == 247
    assert _count(2027) == 247


def test_next_and_nth_workday():
    assert next_workday(date(2026, 5, 9)) == date(2026, 5, 12)
    assert next_workday(date(2026, 5, 12)) == date(2026, 5, 12)
    # выход в пн 2 ноября 2026, 4 ноября — праздник
    assert nth_workday(date(2026, 11, 2), 1) == date(2026, 11, 2)
    assert nth_workday(date(2026, 11, 2), 2) == date(2026, 11, 3)
    assert nth_workday(date(2026, 11, 2), 3) == date(2026, 11, 5)
    # выход в праздник — первый рабочий день следующий
    assert nth_workday(date(2026, 1, 1), 1) == date(2026, 1, 12)
