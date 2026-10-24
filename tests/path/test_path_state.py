"""Состояние «Пути новичка»: 7 вех (знакомство + 6 опросов), статусы, позиция, «пройдено N из 7».

Чистые функции над dialog_data, без БД.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.path import service as P
from app.path import texts as T

CODES = ["day1", "day3", "week2", "plan", "month2", "final"]


def emp(surveys: dict | None = None, *, onboarded: bool = True, site: str = "GTP", **kw):
    data = {"onboarded": onboarded}
    if surveys is not None:
        data["surveys"] = surveys
    base = {"site_code": site, "status": "active", "is_synthetic": False, "address_form": "ty",
            "first_name": "Иван", "dialog_data": data}
    base.update(kw)
    return SimpleNamespace(**base)


def done(at: str = "2026-11-09T10:00:00+00:00") -> dict:
    return {"started_at": at, "done_at": at}


def started(at: str = "2026-11-09T10:00:00+00:00") -> dict:
    return {"started_at": at}


def test_seven_milestones_in_survey_order():
    st = P.path_state(emp({}))
    assert [m.code for m in st.milestones] == ["intro", *CODES]
    assert st.total == 7
    assert [m.label for m in st.milestones] == [
        "Знакомство", "Первый день", "Третий день", "Две недели", "Адаптационный план", "Два месяца",
        "Конец испытательного срока"]
    # названия опросов — из config/surveys.yaml
    assert st.milestones[3].title == "Чек через 2 недели"


def test_intro_counts_right_away_as_head_start():
    st = P.path_state(emp({}))
    assert [m.status for m in st.milestones] == ["done"] + ["ahead"] * 6
    assert st.passed == 1 and st.current == 0 and st.next == 1
    assert st.next_milestone.label == "Первый день"


def test_not_onboarded_has_nothing_passed():
    st = P.path_state(emp({}, onboarded=False, status="waiting"))
    assert st.passed == 0 and st.current is None and st.next == 0


def test_done_surveys_any_answers_count():
    st = P.path_state(emp({"day1": done(), "day3": done()}))
    assert [m.status for m in st.milestones][:4] == ["done", "done", "done", "ahead"]
    assert st.passed == 3 and st.current == 2 and st.next_milestone.code == "week2"


def test_started_but_unfinished_survey_is_still_ahead():
    st = P.path_state(emp({"day1": done(), "day3": started()}))
    assert st.milestones[2].status == "ahead"
    assert st.current == 1 and st.next == 2


def test_skipped_survey_when_later_one_started_or_closed():
    # day1 не прошёл, day3 закрыт новым опросом, week2 идёт: путь движется дальше
    st = P.path_state(emp({"day1": started(), "day3": {"started_at": "x", "closed_at": "y"}, "week2": started()}))
    assert [m.status for m in st.milestones][:4] == ["done", "skipped", "skipped", "ahead"]
    assert st.passed == 1, "пропущенные вехи не считаются пройденными"
    assert st.current == 2 and st.next_milestone.code == "week2"


def test_never_started_survey_skipped_after_time_travel():
    # перемотка сразу на 14-й день: опросы 1-го и 3-го дня не начинались
    st = P.path_state(emp({"week2": done()}))
    assert [m.status for m in st.milestones][:4] == ["done", "skipped", "skipped", "done"]
    assert st.passed == 2 and st.current == 3 and st.next_milestone.code == "plan"


def test_whole_path_done():
    st = P.path_state(emp({c: done() for c in CODES}))
    assert st.passed == 7 and st.current == 6 and st.next is None and st.finished
    assert st.next_milestone is None


def test_final_done_with_skips_is_finished():
    st = P.path_state(emp({"day3": done(), "final": done()}))
    assert st.finished and st.passed == 3
    assert [m.status for m in st.milestones] == ["done", "skipped", "done", "skipped", "skipped", "skipped", "done"]


def test_unknown_site_without_day1_question_drops_milestone():
    # опрос «Доступы в первый день» есть только для GTP, GrPP и Лужников
    st = P.path_state(emp({}, site="NEW"))
    assert "day1" not in [m.code for m in st.milestones] and st.total == 6


def test_progress_line_same_text_for_milestone():
    st = P.path_state(emp({"day1": done(), "day3": done()}))
    assert P.milestone_line(st, "day3") == "Веха 3 из 7 пройдена. Дальше: «Две недели»."
    st0 = P.path_state(emp({}))
    assert P.milestone_line(st0, "intro") == "Веха 1 из 7 пройдена. Дальше: «Первый день»."
    last = P.path_state(emp({c: done() for c in CODES}))
    assert P.milestone_line(last, "final") == T.LINE_LAST.format(n=7, total=7)


def test_hello_line():
    assert P.hello_line(P.path_state(emp({"day1": done()}))) == "Пройдено 2 из 7 вех."


def test_eligible_only_live_onboarded_newcomer(path_on, monkeypatch):
    from app.config import settings

    assert P.eligible(emp({}))
    assert not P.eligible(emp({}, is_synthetic=True))
    assert not P.eligible(emp({}, status="stopped"))
    assert not P.eligible(emp({}, onboarded=False, status="waiting"))
    monkeypatch.setattr(settings, "path_enabled", False)
    assert not P.eligible(emp({}))


def test_no_long_dash_and_no_emoji_in_texts():
    import re

    for name in dir(T):
        value = getattr(T, name)
        for s in (value if isinstance(value, tuple) else (value,)):
            if isinstance(s, str):
                assert "—" not in s, name
                assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", s), name


def test_time_travel_back_forgets_milestone_and_keeps_merch_mark():
    """Перемотка тестового новичка назад забывает опрос (surveys.reset): путь откатывается, отметка HR остаётся."""
    from app.surveys import engine as sv

    e = emp({c: done() for c in CODES})
    e.dialog_data["path"] = {"hr_finished_at": "2027-02-08T10:00:00+00:00"}
    e.dialog_state = None
    sv.reset(e, "final")
    st = P.path_state(e)
    assert not st.finished and st.passed == 6 and st.next_milestone.code == "final"
    assert e.dialog_data["path"]["hr_finished_at"], "второго сообщения про мерч не будет"
