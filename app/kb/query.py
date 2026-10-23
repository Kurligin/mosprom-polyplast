"""Подготовка запроса: синонимы, безопасный tsquery, объединение рангов (RRF)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

SYNONYMS_PATH = Path(__file__).resolve().parents[2] / "config" / "synonyms.yaml"

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _words(block: str) -> list[str]:
    return block.split()


# Стоп-слова: служебные и вопросительные, которые только шумят в поиске
STOP_WORDS = frozenset(_words(
    """
    а и или но да же ли бы то не ни в во на о об от до по за из к ко у с со для при про без над под
    что как кто где когда куда откуда зачем почему сколько какой какая какое какие каков чей
    это этот эта эти тот та те так там тут вот уже еще ещё очень можно нужно надо ли
    я мне меня мной мы нас нам вы вас вам ты тебя тебе он она оно они его ее её их им ему ей
    мой моя мое моё мои наш наша ваш ваша свой своя
    быть есть был была было были будет будут бывает
    если чтобы ну вообще скока скоко сколька
    """
))

# Окончания для грубой основы слова (длинные — раньше)
_ENDINGS = sorted(
    _words("""
    иями ями ами ием ием иях ях ах ов ев ей ой ий ый ая яя ое ее ые ие ую юю ого его ому ему ым им
    ом ем ам ям ую ия ья ье ью ь а я о е ы и у ю ть ться тся ешь ет ут ют ит ат ят ла ло ли л
    """),
    key=len,
    reverse=True,
)


def tokenize(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in _WORD_RE.findall(text or "")]


def stem(word: str) -> str:
    """Грубая основа русского слова — только для синонимов и демо-поиска в памяти."""
    w = word.lower().replace("ё", "е")
    for end in _ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 3:
            return w[: -len(end)]
    return w


def _stems(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text)]


@lru_cache(maxsize=1)
def load_synonyms(path: str | None = None) -> list[list[str]]:
    p = Path(path) if path else SYNONYMS_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [[str(x) for x in g] for g in data.get("groups", [])]


def _contains_phrase(q_stems: list[str], phrase: str) -> bool:
    """Есть ли фраза (по основам слов, подряд) в запросе."""
    p = _stems(phrase)
    if not p:
        return False
    n = len(p)
    return any(q_stems[i : i + n] == p for i in range(len(q_stems) - n + 1))


def synonyms_for(question: str) -> list[str]:
    """Синонимы, которых нет в вопросе, для всех найденных групп."""
    q = _stems(question)
    extra: list[str] = []
    for group in load_synonyms():
        if any(_contains_phrase(q, term) for term in group):
            for term in group:
                if not _contains_phrase(q, term) and term not in extra:
                    extra.append(term)
    return extra


def expand_synonyms(question: str) -> str:
    """Вопрос + синонимы через пробел (для полнотекстового поиска)."""
    extra = synonyms_for(question)
    return question if not extra else f"{question} {' '.join(extra)}"


def build_tsquery(question: str, expand: bool = True) -> str:
    """Безопасная строка для to_tsquery('russian', …): слова через « | ».

    В результат попадают только буквы и цифры — кавычки, скобки, &, |, !, : и
    прочие операторы tsquery отбрасываются. Пустой запрос → "".
    """
    text = expand_synonyms(question) if expand else question
    seen: list[str] = []
    for w in tokenize(text):
        if w in STOP_WORDS or len(w) < 2:
            continue
        if w not in seen:
            seen.append(w)
    return " | ".join(seen)


def rrf_merge(*ranked_lists, k: int = 60) -> list[tuple]:
    """Reciprocal Rank Fusion: списки id по убыванию релевантности → [(id, score)]."""
    scores: dict = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst or [], start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def topic_of(question: str) -> str | None:
    """Тема вопроса для статистики: первое слово найденной группы синонимов («обед», «отпуск»)."""
    q = _stems(question)
    for group in load_synonyms():
        if any(_contains_phrase(q, term) for term in group):
            return group[0]
    return None
