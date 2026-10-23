"""Импорт xlsx ТЗ: все 68 шагов с правилом времени."""
from pathlib import Path

import pytest

from app.scenario.importer import _attachment, _code, parse_xlsx, summary

XLSX = Path("data/raw/korsik_scenario.xlsx")


def test_helpers():
    from datetime import datetime
    assert _code(datetime(2023, 5, 1)) == "1.5"
    assert _code(datetime(2023, 1, 3)) == "3.1"
    assert _attachment("Изображение 4") == "img_04.png"
    assert _attachment("Изображение Открытка №2") == "card_02.png"


@pytest.mark.skipif(not XLSX.exists(), reason="нет data/raw/korsik_scenario.xlsx")
def test_parse_real_xlsx():
    steps = parse_xlsx(XLSX)
    sm = summary(steps)
    assert sm["steps"] == 68
    assert sm["unparsed"] == []
    assert {"GrPP", "Luzhniki", "GTP"} <= set(sm["by_site"])
    by = {s.number: s for s in steps}
    # «Да!» на вопрос про обед ведёт к шагу-ответу
    lunch = next(s for s in steps if s.code == "3.2")
    yes = next(b for b in lunch.buttons if b.label.startswith("Да"))
    assert yes.next_step and by[yes.next_step].timing.event == "answer"
    # ты/вы
    assert {b.action for s in steps for b in s.buttons} >= {"set_address:ty", "set_address:vy"}
    # отмашки рекрутера
    assert sorted(s.gate for s in steps if s.gate) == ["plan_ready", "recruiter_go"]
    # события вне расписания
    assert {s.timing.event for s in steps if s.kind == "event"} == {"free_question", "survey_reminder"}
