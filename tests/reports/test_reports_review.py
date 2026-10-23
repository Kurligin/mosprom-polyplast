"""Сводка руководителю HR: высокий риск только из трёх средних причин — без пустого списка причин."""

from app.reports import render, weekly
from tests.reports.test_reports_risk_zone import MON, item, report

THREE_MEDIUM = item("high", ("yellow_flag", "medium"), ("missed_surveys", "medium"), ("silent", "medium"))


def test_high_from_three_medium_reasons_has_reasons():
    rep = report({"GTP": 5})
    rep["risk_zone"] = weekly.risk_zone(rep, [THREE_MEDIUM], [])
    people = rep["risk_zone"]["people"]
    assert people["reasons"], "у высокого риска из средних причин причины тоже есть"
    assert people["text"] == "1 новичок в зоне высокого риска: жёлтые сигналы, пропуск опросов"
    risk = [ln for ln in weekly.caption(rep, MON).splitlines() if "риск" in ln]
    assert risk == [("В зоне высокого риска 1 новичок: жёлтые сигналы, пропуск опросов. "
                     "Подробно в Mini App, раздел «Аналитика».")]
    assert "risk-why\"></div>" not in render.render_html(rep).replace(" ", "")


def test_caption_without_reasons_has_no_colon():
    zone = {"total": 1, "high": 1, "medium": 0, "reasons": []}
    line = weekly.risk_caption(zone)
    assert ": ." not in line and line.startswith("В зоне высокого риска 1 новичок.")
