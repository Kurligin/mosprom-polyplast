import re

import pytest

from app.kb.query import build_tsquery, expand_synonyms, rrf_merge
from app.kb.route import decide, is_personal_hr_case
from app.llm.gigachat import parse_json_object
from app.privacy.mask import mask

SAFE = re.compile(r"^[а-яёa-z0-9]+( \| [а-яёa-z0-9]+)*$")


@pytest.mark.parametrize(
    "q",
    [
        "Когда 'платят' \"зарплату\"?",
        "обед & перерыв | !отпуск :* (скобки) <-> 'a''b",
        "больничный\\; DROP TABLE kb_chunks; --",
        "«ёлки» — пропуск?!",
    ],
)
def test_build_tsquery_safe(q):
    ts = build_tsquery(q)
    assert ts == "" or SAFE.match(ts), ts


def test_build_tsquery_stopwords_and_empty():
    assert build_tsquery("что как где?", expand=False) == ""
    assert build_tsquery("'&|!():*", expand=False) == ""
    assert build_tsquery("Сколько длится обед", expand=False) == "длится | обед"


def test_expand_synonyms():
    assert "заработная плата" in expand_synonyms("когда зарплата")
    assert "нетрудоспособность" in expand_synonyms("как оформить больничный")
    assert "перерыв" in expand_synonyms("сколько длится обед")
    assert expand_synonyms("абракадабра") == "абракадабра"


def test_rrf_merge():
    res = rrf_merge(["a", "b", "c"], ["b", "a", "d"])
    ids = [i for i, _ in res]
    assert set(ids[:2]) == {"a", "b"} and ids[-1] in {"c", "d"}
    assert rrf_merge() == []


@pytest.mark.parametrize(
    "text",
    [
        "Мне не пришла зарплата за сентябрь",
        "Меня увольняют, что делать?",
        "я заболел, что делать",
        "больничный у меня с понедельника",
        "Мне не оплатили больничный за 2 дня, что делать?",
        "Меня хотят уволить на испытательном, это законно?",
        "У меня конфликт с руководителем, он орёт на меня при всех",
        "Я беременна, меня могут уволить по испытательному?",
        "За зарплату сколько получает мой коллега Иванов?",
        "Мастер заставляет работать без СИЗ, говорит так быстрее",
    ],
)
def test_personal_yes(text):
    assert is_personal_hr_case(text)


@pytest.mark.parametrize(
    "text",
    [
        "Во сколько начинается рабочий день?",
        "скока длится обед",
        "Когда платят зарплату?",
        "Как оформить больничный?",
        "Сколько дней отпуска положено?",
        "Можно ли брать подарки от поставщиков?",
        "Что считается прогулом?",
        "Какие документы нужны при приеме на работу?",
        "Что такое коммерческая тайна?",
        "После увольнения я могу рассказывать о проектах?",
    ],
)
def test_personal_no(text):
    assert not is_personal_hr_case(text)


def test_decide():
    assert decide(0.9, hi=0.8, lo=0.4) == "answer"
    assert decide(0.5, hi=0.8, lo=0.4) == "ask_llm"
    assert decide(0.1, hi=0.8, lo=0.4) == "escalate"
    assert decide(None, hi=0.8, lo=0.4) == "escalate"


def test_mask():
    t = "Иванов Иван Иванович, тел. +7 (916) 123-45-67, ivanov@poly.ru; Иванову передай 89161234567"
    m = mask(t, ["Иванов Иван Иванович"])
    assert "Иван" not in m and "916" not in m and "@" not in m
    assert m.count("[ФИО]") == 2 and m.count("[ТЕЛЕФОН]") == 2 and "[EMAIL]" in m


def test_parse_json_object():
    assert parse_json_object('```json\n{"chunk_id": 3}\n```') == {"chunk_id": 3}
    assert parse_json_object('Ответ: {"chunk_id": null}') == {"chunk_id": None}
    assert parse_json_object("не знаю") is None
    assert parse_json_object('{"chunk_id": 3') is None


def _assert_quote_verbatim(out, text):
    for ln in out.splitlines():
        body = ln[2:] if ln.startswith("> ") else None
        if body and body != "…":
            assert body in text


@pytest.mark.needs_customer_docs
def test_card_excerpt_is_verbatim():
    from app.kb.card import render_card
    from app.kb.chunker import load_all_docs, sub_lines

    c = next(x for x in load_all_docs("docs") if x.point_no == "6.5.2")
    # поиск нашёл строку про зарплату → в карточке вводная строка и она
    focus = next(line for _, line in sub_lines(c) if "29-го числа" in line)
    out = render_card(c.text, c.source_label, "Когда платят зарплату?", focus=focus)
    assert "29-го числа" in out and "Источник: ПВТР, п. 6.5.2, стр. 15" in out
    assert "> 6.5.2. **Работодатель обязан:**" in out
    assert len(out) < len(c.text)
    _assert_quote_verbatim(out, c.text)


@pytest.mark.needs_customer_docs
def test_card_very_long_point_without_focus_is_excerpt():
    from app.kb.card import FULL_LIMIT, render_card
    from app.kb.chunker import load_all_docs

    c = next(x for x in load_all_docs("docs") if x.point_no == "6.4.2")
    assert len(c.text) > FULL_LIMIT
    out = render_card(c.text, c.source_label, "можно ли играть в компьютерные игры")
    assert "компьютерных игр" in out and len(out) < len(c.text)
    _assert_quote_verbatim(out, c.text)