"""Блок «Зоны риска» в еженедельной сводке: без имён и цитат, порог площадки, подпись, вёрстка без переполнения."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.analytics.rules import Reason
from app.reports import render, weekly
from tests.reports.test_reports_weekly import MON, make_raw

NOW = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)


def item(level: str, *codes: tuple[str, str]) -> SimpleNamespace:
    return SimpleNamespace(level=level, reasons=[Reason(code, lv, f"{code} (синтетика)") for code, lv in codes],
                           employee_id=1, full_name="Синтетиков Демьян")


def cluster(label, count, prev, people, sites):
    return {"topic": label.lower(), "label": label, "count": count, "prev": prev, "people": people,
            "sites": [{"code": c, "count": n, "prev": p, "people": pp} for c, n, p, pp in sites],
            "quotes": [{"text": "цитата (синтетика)"}]}


ITEMS = [
    item("high", ("red_flag", "high"), ("yellow_flag", "medium")),
    item("high", ("intent", "high")),
    item("high", ("score_drop", "high"), ("intent", "medium")),
    item("medium", ("yellow_flag", "medium")),
]
CLUSTERS = [
    cluster("Доступы", 9, 3, 6, [("GTP", 7, 2, 5), ("GrPP", 2, 1, 1)]),         # 78% на GTP
    cluster("Нагрузка", 5, 5, 4, [("GTP", 2, 2, 2), ("GrPP", 3, 3, 2)]),        # не растёт
    cluster("Наставник", 4, 0, 3, [("GrPP", 2, 0, 2), ("Luzhniki", 2, 0, 1)]),  # новая тема
    cluster("Коллектив", 6, 1, 1, [("GTP", 6, 1, 1)]),                          # один человек — не тема
    cluster("Документы", 2, 0, 2, [("GTP", 2, 0, 2)]),                          # мало упоминаний
]


def report(sites: dict[str, int]) -> dict:
    return weekly.aggregate(make_raw(sites), weekly.period(MON))


def test_people_line_counts_high_reasons():
    z = weekly.risk_zone(report({"GTP": 5, "GrPP": 5}), ITEMS, CLUSTERS)
    p = z["people"]
    assert (p["total"], p["high"], p["medium"]) == (4, 3, 1)
    # у высокого уровня — только причины высокого уровня: «намерение уйти» у третьего — среднее, не в счёт
    assert p["reasons"] == ["красные сигналы", "намерение уйти"]
    assert p["text"] == "3 новичка в зоне высокого риска: красные сигналы, намерение уйти"


def test_topics_growing_first_site_only_if_in_slice():
    z = weekly.risk_zone(report({"GTP": 5, "GrPP": 5}), ITEMS, CLUSTERS)
    assert [t["text"] for t in z["topics"]] == [
        "Доступы (Площадка GTP): 7 упоминаний, в 4 раза больше, чем месяц назад",
        "Наставник: 4 упоминания, новая тема",
    ]
    # GTP не в срезе (ответили меньше 5): тема по всем площадкам
    z = weekly.risk_zone(report({"GTP": 4, "GrPP": 6}), ITEMS, CLUSTERS)
    assert z["topics"][0]["text"] == "Доступы: 9 упоминаний, в 3 раза больше, чем месяц назад"


def test_no_topics_without_overall_slice():
    z = weekly.risk_zone(report({"GTP": 2, "GrPP": 2}), ITEMS, CLUSTERS)
    assert z["topics"] == [] and z["people"]["high"] == 3


def test_only_medium_and_no_risks():
    z = weekly.risk_zone(report({"GTP": 5}), [item("medium", ("silent", "medium"))], [])
    assert z["people"]["text"] == "1 новичок в зоне риска: молчат"
    assert weekly.risk_zone(report({"GTP": 5}), [], [])["people"]["text"] == "Новичков в зоне риска нет"


def test_risk_zone_has_no_names_or_quotes():
    z = weekly.risk_zone(report({"GTP": 5, "GrPP": 5}), ITEMS, CLUSTERS)
    dumped = json.dumps(z, ensure_ascii=False)
    assert "quote" not in dumped and "Синтетиков" not in dumped and "employee_id" not in dumped
    assert "цитата" not in dumped


def test_caption_has_one_risk_line():
    rep = report({"GTP": 5, "GrPP": 5})
    rep["risk_zone"] = weekly.risk_zone(rep, ITEMS, CLUSTERS)
    lines = weekly.caption(rep, MON).splitlines()
    risk = [ln for ln in lines if "риск" in ln]
    assert risk == [("В зоне высокого риска 3 новичка: красные сигналы, намерение уйти; ещё 1 со средним. "
                     "Подробно в Mini App, раздел «Аналитика».")]
    assert "—" not in "\n".join(lines)
    rep["risk_zone"] = weekly.risk_zone(rep, [], [])
    assert not any("риск" in ln for ln in weekly.caption(rep, MON).splitlines())


def test_render_html_risk_block():
    rep = report({"GTP": 5, "GrPP": 5})
    rep["risk_zone"] = weekly.risk_zone(rep, ITEMS, CLUSTERS)
    html = render.render_html(rep)
    assert "Зоны риска" in html and "новичка в зоне высокого риска" in html
    assert "Площадка GTP" in html and "в 4 раза больше, чем месяц назад" in html
    assert "О чём говорят новички" not in html and "Чаще всего:" in html  # топ тем — одной строкой
    assert "цитата" not in html
    rep["risk_zone"] = None
    assert "О чём говорят новички" in render.render_html(rep)


@pytest.mark.asyncio
async def test_worst_case_layout_fits():
    """4 строки настроения (3 площадки и «Остальные»), длинные названия, подвал в 3 строки, две темы с площадкой."""
    from playwright.async_api import async_playwright

    raw = make_raw({"GTP": 6, "GrPP": 6, "Luzhniki": 6})
    raw.sites += [("Kazan", "Площадка Казань, производство полимерных труб"), ("Tula", "Площадка Тула")]
    for i in range(6):
        code = "Kazan" if i < 3 else "Tula"
        raw.employees.append({"id": 900 + i, "site": code, "start_date": date(2026, 10, 1),
                              "probation_end": date(2027, 1, 1), "status": "active", "synthetic": True})
        raw.answers.append({"employee_id": 900 + i, "site": code, "score": 4 if i < 2 else None, "mood": "negative",
                            "topic": "нагрузка", "synthetic": True})
    rep = weekly.aggregate(raw, weekly.period(MON))
    assert len(rep["mood_rows"]) == 4 and rep["mood_rows"][-1]["avg_score"] is None
    many = [item("high", ("meeting_overdue", "high"), ("not_connected", "high"))] * 12 + ITEMS * 3
    clusters = [cluster("Понимание задач", 19, 3, 8, [("GrPP", 15, 2, 6)]),
                cluster("Быт и транспорт", 12, 2, 5, [("GTP", 12, 2, 5)])]
    rep["risk_zone"] = weekly.risk_zone(rep, many, clusters)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": render.WIDTH, "height": render.HEIGHT})
        await page.set_content(render.render_html(rep), wait_until="load")
        await page.evaluate("document.fonts.ready")
        bottom = await page.evaluate("document.querySelector('.foot').getBoundingClientRect().bottom")
        scroll = await page.evaluate("document.querySelector('.page').scrollHeight")
        await browser.close()
    assert scroll <= render.HEIGHT and bottom <= render.HEIGHT - 24


def test_build_adds_risk_zone(monkeypatch):
    """build: зоны риска считаются на том же окне, что сводка; сломался модуль — сводка без блока."""
    import asyncio

    calls = {}

    async def fake_collect(session, p):
        return make_raw({"GTP": 5, "GrPP": 5})

    async def fake_risks(session, now=None, **kw):
        return ITEMS

    async def fake_clusters(session, weeks=4, now=None, **kw):
        calls.update(weeks=weeks, until=kw.get("until"), quotes=kw.get("quotes"))
        return CLUSTERS

    import app.analytics as A

    monkeypatch.setattr(weekly, "collect", fake_collect)
    monkeypatch.setattr(A, "risks", fake_risks)
    monkeypatch.setattr(A, "clusters", fake_clusters)
    rep = asyncio.run(weekly.build(None, NOW))
    assert rep["risk_zone"]["people"]["high"] == 3
    assert calls == {"weeks": 4, "until": weekly.period(NOW)["to_dt"], "quotes": 0}

    async def broken(*a, **kw):
        raise RuntimeError("сломался")

    monkeypatch.setattr(A, "risks", broken)
    assert asyncio.run(weekly.build(None, NOW + timedelta(minutes=1)))["risk_zone"] is None
