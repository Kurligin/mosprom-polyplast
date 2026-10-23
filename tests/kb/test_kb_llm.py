import asyncio

from app.llm import gigachat as g


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
