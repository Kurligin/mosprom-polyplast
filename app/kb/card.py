"""Карточка ответа: подводка + дословная цитата + источник.

Длинный пункт (перечень обязанностей на страницу) режем до вводной строки
и самых подходящих к вопросу строк — каждая строка остаётся дословной, пропуски — «…».
"""

from __future__ import annotations

from app.kb.query import STOP_WORDS, expand_synonyms, stem, tokenize

LONG_POINT = 800  # символов; длиннее — выдержка вокруг строки, которую нашёл поиск
FULL_LIMIT = 1600  # без найденной строки пункт до этой длины показываем целиком
FOCUS_MIN = 1200  # с найденной строкой: короче — пункт целиком, длиннее — вводная строка и найденная


def _terms(text: str) -> set[str]:
    return {stem(w) for w in tokenize(text) if w not in STOP_WORDS and len(w) > 1}


def excerpt(text: str, question: str, max_lines: int = 2, limit: int = LONG_POINT) -> str:
    """Вводная строка пункта + до max_lines лучших по совпадению строк, дословно."""
    if len(text) <= limit:
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


def focus_excerpt(text: str, focus: str) -> str:
    """Длинный пункт: вводная строка + строка, которую нашёл поиск (дословно), пропуски — «…»."""
    if len(text) <= FOCUS_MIN or not focus:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == focus.strip()), None)
    if idx is None or idx == 0:
        return text
    out = [lines[0]]
    if idx > 1:
        out.append("…")
    out.append(lines[idx])
    if idx != len(lines) - 1:
        out.append("…")
    return "\n".join(out)


def quote_block(text: str) -> str:
    """Цитата для markdown «МАКС»: каждая строка с «> »."""
    return "\n".join("> " + ln if ln.strip() else ">" for ln in text.splitlines())


def render_card(text: str, source_label: str, question: str = "", lead: str = "Вот что сказано в документе:",
                focus: str | None = None) -> str:
    quote = shown_text(text, question, focus)
    return f"{lead}\n\n{quote_block(quote)}\n\nИсточник: {source_label}"


def shown_text(text: str, question: str = "", focus: str | None = None) -> str:
    """Что показываем новичку и что видит LLM при выборе пункта: длинный пункт — выдержкой."""
    if focus:
        return focus_excerpt(text, focus)
    return excerpt(text, question, limit=FULL_LIMIT) if question else text
