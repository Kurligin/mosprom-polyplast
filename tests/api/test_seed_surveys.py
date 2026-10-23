"""Синтетические ответы опросов: каждый ответ — не раньше дня своего опроса по сценарию.

Новичок на 2-м дне не может пройти опрос испытательного срока. Все люди — синтетические.
"""

import json
import random
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from app.signals import rules
from seed import surveys_seed as S
from seed.seed import add_months

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)   # пятница, 15:00 МСК


def _emp(i: int, site: str, days_ago: int) -> SimpleNamespace:
    start = TODAY - timedelta(days=days_ago)
    return SimpleNamespace(id=i, site_code=site, start_date=start, probation_end=add_months(start, 3))


def _rows() -> list[dict]:
    return [json.loads(line) for line in S.SYNTH.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_survey_dates_follow_scenario():
    e = SimpleNamespace(id=1, site_code="GTP", start_date=date(2026, 10, 22), probation_end=date(2027, 1, 22))
    days = {code: S.survey_at(code, e).date() for code in S.SURVEY_TIMING}
    assert days == {
        "day1": date(2026, 10, 22),
        "day3": date(2026, 10, 26),    # 3-й рабочий день: чт, пт, пн
        "week2": date(2026, 11, 5),    # через 2 недели
        "plan": date(2026, 11, 23),    # первый день второго месяца: 22.11 — воскресенье
        "month2": date(2026, 12, 14),  # через месяц и 3 недели: 13.12 — воскресенье
        "final": date(2027, 1, 22),    # последний день испытательного
    }


def test_free_answers_not_before_survey_day():
    emps = [_emp(1, "GTP", 1), _emp(2, "GTP", 20), _emp(3, "GTP", 40), _emp(4, "GTP", 60), _emp(5, "GTP", 97),
            _emp(6, "GrPP", 2), _emp(7, "GrPP", 30), _emp(8, "GrPP", 98), _emp(9, "Luzhniki", 5),
            _emp(10, "Luzhniki", 99)]
    by_site: dict[str, list] = {}
    for e in emps:
        by_site.setdefault(e.site_code, []).append(e)
    rows = _rows()
    placed, skipped = S.assign_free(rows, by_site, NOW, random.Random(7))
    assert placed and skipped == len(rows) - len(placed)
    seen = set()
    for e, row, (code, key, _value, _score), when in placed:
        assert S.survey_at(code, e) <= when <= NOW, (e.id, code, when)
        assert row["site"] == e.site_code
        assert (e.id, key) not in seen  # на вопрос новичок отвечает один раз
        seen.add((e.id, key))
    codes = Counter((e.id, code) for e, _, (code, *_), _ in placed)
    assert not any(code in ("week2", "plan", "month2", "final") for (eid, code) in codes if eid in (1, 6, 9))
    assert {code for (eid, code) in codes if eid == 5} >= {"final"}


def test_red_rows_get_hosts_first():
    """Хозяев мало: пропадают спокойные ответы, а не сигналы."""
    hosts = [_emp(1, "GTP", 20), _emp(2, "GTP", 97)]
    rows = [r for r in _rows() if r["site"] == "GTP"]
    placed, _ = S.assign_free(rows, {"GTP": hosts}, NOW, random.Random(7))

    def red(row):
        return any(h.level == "red" for h in rules.detect(row["text"]))

    red_by_key: Counter = Counter()
    for row in rows:
        if red(row):
            code, key, *_ = S._target(row, "GTP", random.Random(0))
            red_by_key[(code, key)] += 1
    expected = sum(min(n, sum(1 for e in hosts if S.survey_at(code, e) <= NOW))
                   for (code, _key), n in red_by_key.items())
    assert expected >= 5
    assert sum(1 for _, row, *_ in placed if red(row)) == expected


def test_base_answers_not_before_survey_day():
    emps = [_emp(1, "GTP", 1), _emp(2, "GTP", 16), _emp(3, "GrPP", 60), _emp(4, "Luzhniki", 97)]
    plan = S.base_plan(emps, NOW, random.Random(1), skip={(2, "week2")})
    assert plan
    for e, code, when in plan:
        assert S.survey_at(code, e) <= when <= NOW
    got = {(e.id, code) for e, code, _ in plan}
    assert (1, "day3") not in got and (2, "day3") in got and (2, "week2") not in got
    assert (3, "month2") in got and (4, "final") in got
