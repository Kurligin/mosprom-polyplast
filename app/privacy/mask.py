"""Маскирование персональных данных перед отправкой текста в LLM."""

from __future__ import annotations

import re

NAME_LABEL = "[ФИО]"
PHONE_LABEL = "[ТЕЛЕФОН]"
EMAIL_LABEL = "[EMAIL]"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.U)
# +7 / 8, затем 10 цифр с любыми разделителями: +7 (495) 745-68-57, 89161234567, 8 916 123 45 67
_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")


def _name_patterns(names: list[str]) -> list[re.Pattern]:
    """Полное ФИО и отдельные части (с падежными окончаниями), длинные — раньше."""
    parts: set[str] = set()
    full: list[str] = []
    for n in names or []:
        n = " ".join(n.split())
        if not n:
            continue
        full.append(n)
        for p in n.split():
            if len(p) >= 3:
                parts.add(p)
    pats = [re.compile(re.escape(f), re.I) for f in sorted(full, key=len, reverse=True)]
    for p in sorted(parts, key=len, reverse=True):
        # основа без последней гласной + до 3 букв окончания: Иванов → Иванова, Ивановым; Мария → Марии
        base = p[:-1] if p[-1].lower() in "аяоеыийуюь" else p
        pats.append(re.compile(r"(?<![\w])" + re.escape(base) + r"[а-яё]{0,3}(?![\w])", re.I))
    return pats


def mask_with_classes(text: str, names: list[str] | None = None) -> tuple[str, set[str]]:
    """Маскирует и возвращает классы найденных данных (для llm_log)."""
    classes: set[str] = set()
    out = text or ""
    out, n = _EMAIL_RE.subn(EMAIL_LABEL, out)
    if n:
        classes.add("email")
    out, n = _PHONE_RE.subn(PHONE_LABEL, out)
    if n:
        classes.add("phone")
    for pat in _name_patterns(names or []):
        out, n = pat.subn(NAME_LABEL, out)
        if n:
            classes.add("name")
    # склеиваем «[ФИО] [ФИО]» после замены частей
    out = re.sub(r"(\[ФИО\])(\s+\[ФИО\])+", NAME_LABEL, out)
    return out, classes


def mask(text: str, names: list[str] | None = None) -> str:
    """ФИО из карточек, телефоны и e-mail → метки [ФИО], [ТЕЛЕФОН], [EMAIL]."""
    return mask_with_classes(text, names)[0]
