"""Разбор текстов времени из колонки «Месяц/день/время информирования»."""
import pytest

from app.scenario.timing import extract_time, is_office_only, is_time_only, parse_timing

# Все варианты, встречающиеся в xlsx ТЗ (лист «27.02.2026»)
CASES = [
    ("За 2-3 дня до выхода на работу", dict(kind="before_start", days_before=3, time=None)),
    ("За день до выхода на работу", dict(kind="before_start", days_before=1)),
    ("В первый рабочий день в 10:00", dict(kind="workday", n=1, time="10:00")),
    ("В первый рабочий день в 11:00", dict(kind="workday", n=1, time="11:00")),
    ("В первый рабочий день в 12:01", dict(kind="workday", n=1, time="12:01")),
    ("В первый рабочий день В 12.30", dict(kind="workday", n=1, time="12:30")),
    ("В первый рабочий день 13.00", dict(kind="workday", n=1, time="13:00")),
    ("В первый рабочий день В 15.00", dict(kind="workday", n=1, time="15:00")),
    ("В первый рабочий день В 18:00 для сотрудников офиса", dict(kind="workday", n=1, time="18:00")),
    ("Во второй рабочий день 10:00", dict(kind="workday", n=2, time="10:00")),
    ("В третий рабочий день в 09.00", dict(kind="workday", n=3, time="09:00")),
    ("В третий рабочий день в 11.00", dict(kind="workday", n=3, time="11:00")),
    ("В третий рабочий день В 17:30", dict(kind="workday", n=3, time="17:30")),
    ("Через 10 дней после выхода на работу", dict(kind="calendar_offset", days=10, weeks=0, months=0)),
    ("Через 2 недели после выхода на работу", dict(kind="calendar_offset", weeks=2, months=0)),
    ("Первый день второго месяца работы в 09.00. ", dict(kind="month_start", month=2, time="09:00")),
    ("Через 1 месяц и  2 недели", dict(kind="calendar_offset", months=1, weeks=2, time=None)),
    ("Через 1 месяц и  3 недели", dict(kind="calendar_offset", months=1, weeks=3)),
    ("Через 1 месяц и  3 недели. В 09.01", dict(kind="calendar_offset", months=1, weeks=3, time="09:01")),
    ("Первый день третьего месяца в 09.00", dict(kind="month_start", month=3, time="09:00")),
    ("Через 2 месяца и 2 недели. Для офисных сотрудников", dict(kind="calendar_offset", months=2, weeks=2)),
    ("Через 2 месяца и 2 недели", dict(kind="calendar_offset", months=2, weeks=2)),
    ("Последний день испытат. срока в 09.00", dict(kind="probation_end", offset_days=0, time="09:00")),
    ("Последний день ИС", dict(kind="probation_end", time=None)),
    ("В случае варианта ответа\n «Да»", dict(kind="event", event="answer", answer="Да")),
    ("Если сотрудник задаст вопрос Корсику (просто так) ", dict(kind="event", event="free_question")),
    ("Если Корсик запустил опрос, а сотрудник НЕ прошел его в течение 1 дня ",
     dict(kind="event", event="survey_reminder")),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_parse_timing(raw, expected):
    rule = parse_timing(raw)
    assert rule is not None, raw
    for k, v in expected.items():
        assert getattr(rule, k) == v, (raw, k)


def test_time_only_and_empty():
    assert parse_timing("В 09.01") is None and is_time_only("В 09.01")
    assert extract_time("В 09.01") == "09:01"
    assert parse_timing(None) is None and parse_timing("  ") is None
    assert parse_timing("когда-нибудь потом") is None


def test_office_only():
    assert is_office_only("В первый рабочий день В 18:00 для сотрудников офиса")
    assert is_office_only(None, "Адаптационный план. Для офисных сотрудников")
    assert is_office_only("Через 2 месяца и 2 недели. Для офисных сотрудников")
    assert not is_office_only("Через 2 месяца и 2 недели")
