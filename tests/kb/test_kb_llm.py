import asyncio

import pytest

from app.llm import gigachat as g
from app.privacy import names as privacy_names


def _fake(responses, seen):
    it = iter(responses)

    async def complete(system, user, temperature=0.01):
        seen.append(user)
        return next(it)

    return complete


def test_choose_chunk_retry_and_validate(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(["не JSON", '{"chunk_id": 7}'], seen))
    cands = [{"chunk_id": 7, "text": "пункт 7"}, {"chunk_id": 9, "text": "секрет", "is_kt": True}]
    assert asyncio.run(g.choose_chunk("звоните +7 916 123 45 67", cands)) == 7
    assert len(seen) == 2  # один повтор
    assert "секрет" not in seen[0] and "916" not in seen[0]  # КТ не уходит, телефон замаскирован


def test_choose_chunk_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr(g, "_complete", _fake(['{"chunk_id": 42}'], []))
    assert asyncio.run(g.choose_chunk("вопрос", [{"chunk_id": 1, "text": "x"}])) is None


def test_choose_chunk_only_kt_no_call(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake([], seen))
    assert asyncio.run(g.choose_chunk("вопрос", [{"chunk_id": 1, "text": "x", "is_kt": True}])) is None
    assert seen == []


def test_analyze_answer_quote_check(monkeypatch):
    text = "Наставник всё объяснил, но пропуск до сих пор не сделали"
    monkeypatch.setattr(
        g, "_complete", _fake(['{"topic": "доступы", "mood": "негатив", "quote": "выдумка"}'], [])
    )
    r = asyncio.run(g.analyze_answer(text))
    assert r == {"topic": "доступы", "mood": "негатив", "quote": None}
    monkeypatch.setattr(
        g, "_complete", _fake(['{"topic": "доступы", "mood": "негатив", "quote": "пропуск до сих пор не сделали"}'], [])
    )
    assert asyncio.run(g.analyze_answer(text))["quote"] == "пропуск до сих пор не сделали"


def test_analyze_answer_health_not_sent(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake([], seen))
    assert asyncio.run(g.analyze_answer("у меня болит спина после смены")) is None
    assert seen == []


# --- приватность: здоровье и маскирование (тексты синтетические) ---

STAFF = ["Петров Сергей Викторович", "Смирнова Ольга (наставник, демо)"]


@pytest.fixture(autouse=True)
def _no_db_names(monkeypatch):
    """Имена сотрудников из БД в этих тестах подменяем: LLM-клиент не ходит в базу."""
    async def fake(*a, **k):
        return list(STAFF)

    monkeypatch.setattr(privacy_names, "known_names", fake)


@pytest.mark.parametrize("question", [
    "я простыл, можно ли не выходить на смену?",
    "у меня мигрень, можно уйти пораньше?",
    "я инвалид 3 группы, положен ли доп отпуск?",
    "если простыл, можно не выходить на смену?",       # без «я»: всё равно здоровье
    "положен ли отпуск при беременности?",
])
def test_choose_chunk_health_not_sent(monkeypatch, question):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(['{"chunk_id": 1}'], seen))
    assert asyncio.run(g.choose_chunk(question, [{"chunk_id": 1, "text": "x"}], raise_errors=True)) is None
    assert seen == []


def test_choose_chunk_general_sick_leave_question_sent(monkeypatch):
    """Общий вопрос о порядке — не о здоровье человека: уходит в выбор пункта."""
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(['{"chunk_id": 1}'], seen))
    assert asyncio.run(g.choose_chunk("больничный кто оплачивает, компания или фонд?",
                                      [{"chunk_id": 1, "text": "x"}])) == 1
    assert len(seen) == 1


@pytest.mark.parametrize("text", [
    "сильно устаю, бессонница, пью таблетки",
    "после ночных смен панические атаки",
    "я простыл и всё равно вышел",
])
def test_analyze_answer_health_variants_not_sent(monkeypatch, text):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake([], seen))
    assert asyncio.run(g.analyze_answer(text)) is None
    assert seen == []


def test_configurable_sensitive_list_respected(monkeypatch):
    """Список sensitive из config/flag_rules.yaml (правит HR) тоже не пускает текст в LLM."""
    from app.signals import rules

    seen = []
    monkeypatch.setattr(g, "_complete", _fake([], seen))
    monkeypatch.setattr(rules, "is_sensitive", lambda text, rules=None: "особое слово" in text)
    assert asyncio.run(g.analyze_answer("тут особое слово из настроек")) is None
    assert seen == []


def test_analyze_answer_masks_all_staff_names_and_phones(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(['{"topic": "руководитель", "mood": "негатив", "quote": null}'], seen))
    text = ("Руководитель Петров Сергей Викторович на меня постоянно кричит при всех, "
            "наставник Ольга Смирнова не отвечает, её номер 916 123-45-67")
    r = asyncio.run(g.analyze_answer(text, ["Новиков Тимофей"]))
    assert r["topic"] == "руководитель"
    sent = seen[0]
    for leak in ("Петров", "Сергей", "Викторович", "Ольга", "Смирнова", "916"):
        assert leak not in sent, (leak, sent)
    assert "[ФИО]" in sent and "[ТЕЛЕФОН]" in sent


def test_choose_chunk_masks_staff_names_without_caller_names(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "_complete", _fake(['{"chunk_id": 1}'], seen))
    q = "Петрову можно звонить на 9161234567 после смены?"
    assert asyncio.run(g.choose_chunk(q, [{"chunk_id": 1, "text": "пункт про звонки"}])) == 1
    assert "Петров" not in seen[0] and "9161234567" not in seen[0]
