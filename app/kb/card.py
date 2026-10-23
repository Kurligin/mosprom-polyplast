"""Карточка ответа: подводка + дословная цитата + источник.

Длинный пункт (перечень обязанностей на страницу) режем до вводной строки
и самых подходящих к вопросу строк — каждая строка остаётся дословной, пропуски — «…».
"""

from __future__ import annotations

from app.kb.query import STOP_WORDS, expand_synonyms, stem, tokenize

LONG_POINT = 800  # символов; длиннее — показываем выдержку


def _terms(text: str) -> set[str]:
    return {stem(w) for w in tokenize(text) if w not in STOP_WORDS and len(w) > 1}


def excerpt(text: str, question: str, max_lines: int = 2) -> str:
    """Вводная строка пункта + до max_lines лучших по совпадению строк, дословно."""
    if len(text) <= LONG_POINT:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return text
    q = _terms(expand_synonyms(question))
    scored = [(len(q & _terms(ln)), i) for i, ln in enumerate(lines[1:], start=1)]
    top = max((s for s, _ in scored), default=0)
    if top == 0:
        return text
    # только строки с лучшим совпадением — случайные пересечения по одному слову не тащим
    best = sorted([x for x in scored if x[0] == top], key=lambda x: x[1])[:max_lines]
    keep = sorted({0, *(i for _, i in best)})
    out, prev = [], -1
    for i in keep:
        if prev >= 0 and i != prev + 1:
            out.append("…")
        out.append(lines[i])
        prev = i
    if keep[-1] != len(lines) - 1:
        out.append("…")
    return "\n".join(out)


def render_card(text: str, source_label: str, question: str = "", lead: str = "Вот что сказано в документе:") -> str:
    quote = excerpt(text, question) if question else text
    quoted = "\n".join("> " + ln if ln.strip() else ">" for ln in quote.splitlines())
    return f"{lead}\n\n{quoted}\n\nИсточник: {source_label}"
