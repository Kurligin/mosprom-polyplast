"""config/surveys.yaml: все опросы ТЗ, тексты дословно, шаги сценария, ветки и флаги."""

import re
from pathlib import Path

import pytest

from app.surveys import config as C
from app.surveys import engine

XLSX = Path("data/raw/korsik_scenario.xlsx")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def test_all_surveys_and_steps():
    surveys = C.load()
    assert set(surveys) == {"day1", "day3", "week2", "plan", "month2", "final"}
    steps = C.step_map()
    for n in (37, 38, 39, 45, 53, 54, 55, 60, 61, 62, 63):
        assert steps.get(n), f"опросный шаг {n} без опроса"
    assert engine.survey_for_step(47) == "plan" and engine.survey_for_step(20) == "day1"
    assert engine.survey_for_step(1) is None and engine.survey_for_step("x") is None


def test_month2_intro_is_one_message():
    """Шаги 51 («Привет!» с картинкой) и 52 — подводка опроса month2 в том же сообщении, как 36 у day3."""
    sv = C.load()["month2"]
    assert engine.survey_for_step(51) == engine.survey_for_step(52) == "month2"
    assert sv.image == "img_08.png"
    assert sv.intro.startswith("Привет!\nМы подготовили небольшой опрос")


def test_question_kinds_and_counts():
    s = C.load()
    assert [q.kind for q in s["day3"].questions] == ["choice", "choice", "choice"]
    assert [q.kind for q in s["week2"].questions] == ["yes_no", "yes_no"] + ["scale"] * 7
    assert [q.kind for q in s["month2"].questions] == ["choice", "multi", "choice_other"]
    final = s["final"].questions
    assert len(final) == 12 and final[0].kind == "choice_other" and final[-1].kind == "open"
    assert all(q.reason_below == 8 for q in s["week2"].questions if q.kind == "scale")
    no = [o for q in s["week2"].questions if q.kind == "yes_no" for o in q.options if o.label == "Нет"]
    assert no and all(o.ask == "reason" for o in no)


def test_flag_triggers_from_dev_plan():
    flags = {o.label: o.flag for sv in C.load().values() for q in sv.questions for o in q.options if o.flag}
    assert flags["Нет, задумываюсь об уходе"]["topic"] == "намерение уйти"
    for label in ("Уже устал, а задачи все валятся", "Никто!", "Пока ничего не понял", "Я отстаю от графика."):
        assert flags[label]["level"] == "yellow" and flags[label]["topic"]


def test_payloads_fit_128():
    for sv in C.load().values():
        for i, q in enumerate(sv.questions):
            for o in q.options:
                for a in o.actions:
                    assert len(f"sv:{sv.code}:{i}:{engine.ACT[a.do]}") <= 128
                    assert len(a.label) <= 128
            assert len(f"sv:{sv.code}:{i}:o:{len(q.options)}") <= 128


@pytest.mark.skipif(not XLSX.exists(), reason="нет xlsx сценария")
def test_texts_verbatim_from_scenario():
    from app.scenario.importer import parse_xlsx

    steps = {s.number: s for s in parse_xlsx(XLSX)}
    for sv in C.load().values():
        source = _norm(" ".join(steps[n].text + " " + " ".join(b.label + " " + (b.reply or "") for b in steps[n].buttons)
                                for n in sv.steps))
        for q in sv.questions:
            assert _norm(q.text).rstrip("?") in source, f"{sv.code}.{q.key}: текст не из ТЗ"
            if q.legend:
                assert _norm(q.legend) in source, f"{q.key}: подпись шкалы"
            for o in q.options:
                assert _norm(o.label) in source, f"{q.key}: вариант «{o.label}»"
                if o.reply:
                    for line in o.reply.splitlines():
                        assert _norm(line) in source, f"{q.key}: ответ «{line[:40]}…»"
        for line in (sv.intro or "").splitlines():
            assert _norm(line) in source, f"{sv.code}: подводка «{line[:40]}…»"
        if sv.outro:
            assert all(_norm(line) in _norm(" ".join(s.text for s in steps.values())) for line in sv.outro.splitlines())


def test_bad_config_rejected():
    with pytest.raises(C.SurveyConfigError):
        C.parse({"surveys": [{"code": "x", "questions": [{"key": "a", "kind": "scale", "text": "t",
                                                            "options": ["1"]}]}]})
    with pytest.raises(C.SurveyConfigError):
        C.parse({"surveys": [{"code": "x", "steps": [1], "questions": [{"key": "a", "kind": "open", "text": "t"}]},
                             {"code": "y", "steps": [1], "questions": [{"key": "a", "kind": "open", "text": "t"}]}]})
