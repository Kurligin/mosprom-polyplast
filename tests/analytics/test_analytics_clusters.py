"""Кластеры тем без БД: что считать упоминанием, динамика, выбор и обрезка цитат."""

from datetime import UTC, datetime, timedelta

from app.analytics.clusters import (
    aggregate,
    clip,
    is_mention,
    pick_quotes,
    trend,
    trend_text,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
LO = NOW - timedelta(weeks=4)


def row(eid, topic, *, mood="negative", text=None, days=1, site="GTP", score=None, ev=None):
    return {"id": eid * 100 + days, "employee_id": eid, "site": site, "topic": topic, "mood": mood, "score": score,
            "text": text, "evidence_quote": ev, "created_at": NOW - timedelta(days=days), "synthetic": True}


def test_is_mention():
    assert is_mention({"mood": "negative"})
    assert is_mention({"mood": "neutral"})
    assert not is_mention({"mood": "positive"})
    assert is_mention({"mood": None, "text": "Нет пропуска"})       # свой текст без разбора — трудность
    assert not is_mention({"mood": "positive", "text": "Всё отлично"})
    assert is_mention({"mood": None, "score": 4})                   # оценка 4 — негатив
    assert not is_mention({"mood": None, "score": None})


def test_trend_text():
    assert trend_text(7, 3) == "в 2 раза больше, чем месяц назад"
    assert trend_text(6, 4) == "в 1,5 раза больше, чем месяц назад"
    assert trend_text(15, 3) == "в 5 раз больше, чем месяц назад"
    assert trend_text(4, 3) == "на 1 больше, чем месяц назад"
    assert trend_text(3, 3) == "столько же, сколько месяц назад"
    assert trend_text(2, 5) == "на 3 меньше, чем месяц назад"
    assert trend_text(4, 0) == "новая тема"
    assert trend_text(3, 2) == "на 1 больше, чем месяц назад"  # +1 — не «в 1,5 раза»
    assert trend_text(4, 2, weeks=2) == "в 2 раза больше, чем за прошлые 2 недели"
    assert [trend(7, 3), trend(1, 4), trend(3, 3), trend(2, 0)] == ["up", "down", "flat", "new"]


def test_clip():
    assert clip("  коротко   и ясно ") == "коротко и ясно"
    long = "слово " * 60
    out = clip(long, 50)
    assert len(out) <= 51 and out.endswith("…") and not out.endswith(" …")


def test_aggregate_counts_sites_and_order():
    rows = [
        row(1, "доступы", days=2), row(2, "доступы", days=3), row(3, "доступы", days=4, site="GrPP"),
        row(1, "доступы", days=40),
        row(4, "нагрузка", days=5), row(4, "нагрузка", days=6),
        row(5, "Другое", days=1), row(6, "коллектив", mood="positive", days=1),
        row(7, "наставник", days=35),  # только в прошлом периоде
    ]
    out = aggregate(rows, LO)
    assert [c["topic"] for c in out] == ["доступы", "нагрузка", "наставник"]
    acc = out[0]
    assert (acc["label"], acc["count"], acc["people"], acc["prev"], acc["negative"]) == ("Доступы", 3, 3, 1, 3)
    assert [(s["code"], s["count"], s["prev"]) for s in acc["sites"]] == [("GTP", 2, 1), ("GrPP", 1, 0)]
    assert out[1]["people"] == 1
    assert out[2]["count"] == 0 and out[2]["trend_text"] == "на 1 меньше, чем месяц назад"


def test_quotes_prefer_negative_unmasked_fresh_one_per_person():
    rows = [
        row(1, "доступы", mood="neutral", text="Доступы дали к обеду, нормально", days=1),
        row(2, "доступы", text="Учётки нет третий день, сижу без дела", days=3),
        row(2, "доступы", text="До сих пор без почты", days=2),
        row(3, "доступы", text="Главная беда в том, что никто не отвечает", days=1),
        row(4, "доступы", text="Нет согласия на разбор, цитаты не будет", days=1),
    ]
    q = pick_quotes(rows, {1, 2, 3}, ["Главная Ольга"], limit=2)
    # у новичка 3 после маскирования осталась метка — его цитата ниже; от новичка 2 — самая свежая
    assert [x["text"] for x in q] == ["До сих пор без почты", "[ФИО] беда в том, что никто не отвечает"]
    assert {x["mood"] for x in q} == {"negative"}


def test_quote_uses_ai_evidence_only_if_it_is_in_text():
    rows = [row(1, "наставник", text="Наставник пропал. Неделю его не видел, задачи не ставят.",
                ev="Неделю его не видел")]
    assert pick_quotes(rows, {1}, [])[0]["text"] == "Неделю его не видел"
    rows[0]["evidence_quote"] = "выдуманная цитата"
    assert pick_quotes(rows, {1}, [])[0]["text"].startswith("Наставник пропал.")


def test_health_and_label_only_quotes_are_skipped():
    rows = [row(1, "нагрузка", text="У меня мигрень после ночных смен"), row(2, "нагрузка", text="Пётр")]
    assert pick_quotes(rows, {1, 2}, ["Пётр Петров"]) == []
