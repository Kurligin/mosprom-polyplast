"""Синтетические вопросы для «Частых тем»: окно 6 недель, дни адаптации по теме, маршруты как у бота."""

import json
import random
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from seed import questions_seed as Q


def _golden() -> list[dict]:
    return [json.loads(line) for line in Q.GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_every_golden_question_has_topic_or_is_personal():
    for g in _golden():
        assert g["category"] in Q.ROUTES
        if g["category"] != "escalate":
            assert g["id"] in Q.TOPICS, g["id"]
            assert Q.TOPICS[g["id"]] in Q.PROFILE


def test_plan_rows_window_routes_and_peaks():
    today = date(2026, 10, 23)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    emps = [SimpleNamespace(id=i, site_code=site, start_date=today - timedelta(days=d))
            for i, (site, d) in enumerate([("GTP", 1), ("GTP", 9), ("GrPP", 16), ("Luzhniki", 25),
                                           ("GTP", 33), ("GrPP", 40), ("Luzhniki", 4)], start=1)]
    rows = Q.plan_rows(_golden(), emps, today, now, random.Random(1))
    assert len(rows) > 100
    starts = {e.id: e.start_date for e in emps}
    for r in rows:
        assert r["route"] in ("answer", "unknown", "personal")
        assert (r["topic"] is None) == (r["route"] == "personal")
        assert now - timedelta(days=Q.WINDOW_DAYS + 1) <= r["created_at"] <= now
        day = (r["created_at"].date() - starts[r["employee_id"]]).days + 1
        lo, hi = Q.PROFILE[r["topic"]][1:]
        assert lo - 1 <= day <= hi + 1, (r["topic"], day)  # ±1: часовой пояс и вечерние вопросы
    top = [t for t, _ in Counter(r["topic"] for r in rows if r["topic"]).most_common(3)]
    assert "зарплата" in top and "доступы" in top
