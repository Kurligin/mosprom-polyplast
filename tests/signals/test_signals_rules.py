"""Словарь флагов: темы, отрицания, свободный текст, чувствительные темы."""

import pytest

from app.signals import rules


def cats(text: str, **kw) -> set[str]:
    return {h.category for h in rules.detect(text, **kw)}


@pytest.mark.parametrize("text,expected", [
    ("Мастер угрожает, что найдёт меня после работы", {"threats"}),
    ("Бригадир сказал: ещё раз опоздаешь, побью", {"threats"}),
    ("Начальник смены ударил меня в раздевалке", {"threats"}),
    ("Коллеги издеваются и унижают при всех", {"bullying"}),
    ("Мастер постоянно орёт и матерится", {"bullying"}),
    ("Старшие смеются надо мной, когда спрашиваю", {"bullying"}),
    ("Обучение по охране труда так и не провели, а к станку уже поставили", {"safety"}),
    ("Беруши выдали только через месяц", {"safety"}),
    ("Заставляют работать без защитных очков", {"safety"}),
    ("В цеху +12 с утра, руки мёрзнут", {"safety"}),
    ("На линии сработала защита, а меня оставили одного", {"safety"}),
    ("Третий день болит спина, на смене кружится голова", {"health"}),
    ("Уже хожу на собеседования, если честно", {"intent_to_leave"}),
    ("Не вижу смысла продолжать", {"intent_to_leave"}),
])
def test_categories(text, expected):
    assert cats(text) == expected


@pytest.mark.parametrize("text", [
    "Никто не угрожает, всё спокойно",
    "Меня не обижают, коллеги нормальные",
    "Инструктаж по охране труда провели в первый день, всё понятно",
    "Выдали спецовку и ботинки, всё по размеру",
    "Нет инструкции, как зайти на корпоративный портал",
    "Всё отлично, спасибо!",
    "Хотелось бы больше задач и наставника",
    "Приходится уходить с работы позже",
])
def test_no_false_alarm(text):
    assert cats(text) == set()


def test_levels_and_quote():
    hits = rules.detect("Всё нормально. Но мастер орёт на меня при всех! Остальное ок")
    assert len(hits) == 1
    h = hits[0]
    assert (h.category, h.topic, h.level, h.notice) == ("bullying", "травля", "red", True)
    assert h.quote == "Но мастер орёт на меня при всех!"
    leave = rules.detect("Ищу другую работу")[0]
    assert (leave.level, leave.notice) == ("yellow", False)


def test_severity_order():
    hits = rules.detect("Мастер орёт, а вчера пригрозил: убью")
    assert [h.category for h in hits] == ["threats", "bullying"]


def test_free_text_needs_first_person_and_skips_health():
    assert cats("Что делать при травме на производстве?", free_text=True) == set()
    assert cats("Меня бьёт мастер", free_text=True) == {"threats"}
    assert cats("Я заболел, у меня болит горло", free_text=True) == set()
    assert cats("Я заболел, у меня болит горло") == {"health"}
    assert cats("Я ищу другую работу", free_text=True) == set(), "намерение уйти в свободном тексте не ловим"


def test_sensitive():
    assert rules.is_sensitive("Был на больничном, никто не объяснил, как оформить")
    assert rules.is_sensitive("Кружится голова и тошнит")
    assert not rules.is_sensitive("Не хватает наставника")


def test_free_text_first_person_verb_is_personal():
    """«Если пожалуюсь» — о себе, хотя «я» в тексте нет: красный флаг в свободном сообщении."""
    text = "мастер угрожает уволить, если пожалуюсь на сломанный кожух"
    assert cats(text, free_text=True) == {"threats", "safety"}
    assert cats("Боюсь, бригадир заставит работать без ограждения", free_text=True) == {"safety"}
    assert cats("Хочу сказать, что мастер угрожает новичкам", free_text=True) == {"threats"}


def test_free_text_questions_without_first_person_not_flagged():
    assert cats("угрожает ли что-то при работе на станке?", free_text=True) == set()
    assert cats("Скажите, кожух на станке обязателен?", free_text=True) == set()
    assert cats("К краю склада подходить опасно?", free_text=True) == set(), "«к краю» — не первое лицо"
    assert cats("Мастер сказал, что без кожуха на станке работать нельзя?", free_text=True) == set()
    # глагол первого лица в вопросе к базе знаний — не жалоба
    assert cats("Могу ли работать на прессе без допуска?", free_text=True) == set()
    assert cats("Хочу узнать, какие травмы бывают на производстве", free_text=True) == set()
    assert cats("Интересуюсь, опасно ли на прессе", free_text=True) == set()
    assert cats("Опасно ли ездить в Беларусь в командировку", free_text=True) == set()


def test_broken_guard_is_safety():
    assert cats("На станке сломанный кожух") == {"safety"}
    assert cats("Защитный экран на прессе сломан") == {"safety"}


def test_free_text_threat_with_question_mark_still_flagged():
    """Знак «?» в жалобе на угрозу — не вопрос к базе знаний: «Что делать, если мастер угрожает уволить?»"""
    assert "threats" in cats("Мастер угрожает уволить, если пожалуюсь на сломанный кожух?", free_text=True)
    assert "threats" in cats("что делать, если мастер угрожает уволить, если пожалуюсь?", free_text=True)
    # явные маркеры вопроса к базе знаний по-прежнему гасят глагол первого лица
    assert cats("Хочу узнать, угрожает ли что-то при работе на станке?", free_text=True) == set()
    # для остальных тем «?» без «я» — по-прежнему вопрос
    assert cats("Боюсь, кожух на станке обязателен?", free_text=True) == set()
