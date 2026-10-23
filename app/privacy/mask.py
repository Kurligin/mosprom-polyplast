"""Маскирование персональных данных перед отправкой текста в LLM.

- ФИО: все переданные имена (LLM-клиент добавляет имена всех сотрудников из БД, см. names.py) — полностью
  и по частям, в любом падеже: «Петров» → «Петрову», «Петровым»; «Вишневский» → «Вишневского»;
  «Павел» → «Павла»; «ё» и «е» не различаются. Пометки в скобках («(HR, демо)», «[синтетика]») и роли
  («руководитель», «новичок») — не имена;
- телефоны в любом виде: с +7/8/7 и без, со скобками, пробелами и дефисами, городские семизначные;
- e-mail;
- номера документов: паспорт, СНИЛС, ИНН, банковская карта, любые длинные номера (10+ цифр).
"""

from __future__ import annotations

import re
from functools import lru_cache

NAME_LABEL = "[ФИО]"
PHONE_LABEL = "[ТЕЛЕФОН]"
EMAIL_LABEL = "[EMAIL]"
DOC_LABEL = "[НОМЕР]"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)

_D = r"(?<![\d+])"   # слева не цифра и не «+»
_E = r"(?!\d)"       # справа не цифра
_S = r"[\s\-‐–]*"     # необязательные разделители

# Банковская карта: 16 цифр, группы по 4
_CARD_RE = re.compile(_D + r"\d{4}(?:[\s\-]?\d{4}){3}" + _E)
# СНИЛС с разделителями: 123-456-789 01
_SNILS_RE = re.compile(_D + r"\d{3}[\s\-]\d{3}[\s\-]\d{3}[\s\-]+\d{2}" + _E)
_PHONE_RE = re.compile(_D + "(?:" + "|".join([
    # +7 / 8 / 7 и 10 цифр: +7 (495) 745-68-57, 89161234567, 8 916 123 45 67
    r"(?:\+\s*7|8|7)[\s\-()]*\(?\d{3}\)?" + _S + r"\d{3}" + _S + r"\d{2}" + _S + r"\d{2}",
    # код в скобках: (495) 745-68-57, (4842) 12-34-56
    r"\(\d{3,5}\)" + _S + r"\d{1,3}" + _S + r"\d{2}" + _S + r"\d{2}",
    # мобильный без префикса: 916 123-45-67, 9161234567
    r"9\d{2}" + _S + r"\d{3}" + _S + r"\d{2}" + _S + r"\d{2}",
    # 10 цифр группами с разделителями: 495 745 68 57
    r"\d{3}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}",
    # городской: 745-68-57, 745 68 57
    r"\d{3}-\d{2}-\d{2}", r"\d{3} \d{2} \d{2}",
]) + ")" + _E)
# Паспорт: 4508 123456, 45 08 123456, 4508 № 123456
_PASSPORT_RE = re.compile(_D + r"\d{2}\s?\d{2}\s*№?\s*\d{6}" + _E)
# Номер после слова: «серия 45 08», «номер 123456», «№ 123456»
_SERIES_RE = re.compile(r"(?i)(серия|сер\.)(\s*:?\s*)\d{2}\s?\d{2}" + _E)
_NUMBER_RE = re.compile(r"(?i)(номер|№|no\.)(\s*:?\s*)\d{6}" + _E)
# Любой длинный номер: ИНН, СНИЛС без разделителей, счёт, ОГРН
_LONG_RE = re.compile(_D + r"\d{10,}" + _E)

# --- ФИО ---------------------------------------------------------------------------

# Не имена: роли, пометки демо, служебные слова из карточек
_STOP = {
    "тест", "тестовый", "тестовая", "новичок", "новичка", "демо", "синтетика", "рекрутер", "руководитель",
    "наставник", "глава", "подбора", "сотрудник", "сотрудница", "оператор", "мастер", "менеджер",
}
# Беглая гласная: Павел → Павла, Лев → Льва
_FLEETING = {"павел": "павл", "лев": "льв"}
_NOUN_END = ("а", "я", "у", "ю", "ы", "и", "е", "ой", "ей", "ою", "ею", "ом", "ем", "ым", "им", "ых", "их",
             "ыми", "ими", "ам", "ям", "ами", "ями", "ах", "ях", "ов", "ев", "ью")
_ADJ_END = ("ий", "ый", "ой", "ая", "яя", "ое", "ее", "ого", "его", "ому", "ему", "ым", "им", "ом", "ем",
            "ую", "юю", "ые", "ие", "ых", "их", "ыми", "ими", "ей", "ою", "ею")
_NOTE_RE = re.compile(r"[\[(][^\])]*[\])]")
_CYR = re.compile(r"^[а-яё]+$")
_LAT = re.compile(r"^[a-z]+$")


def clean_name(name: str) -> str:
    """ФИО без пометок в скобках: «Тестова Анна (HR, демо)» → «Тестова Анна»."""
    return " ".join(_NOTE_RE.sub(" ", name or "").split())


def _yo(s: str) -> str:
    """В шаблоне «е» и «ё» — одна буква."""
    return s.replace("ё", "е").replace("е", "[её]")


def _alt(items) -> str:
    return "|".join(sorted(set(items), key=lambda x: (-len(x), x)))


def _word_parts(names: tuple[str, ...]) -> tuple[set[str], set[str], set[str]]:
    """Части ФИО → (точные слова, основы существительных, основы прилагательных)."""
    exact: set[str] = set()
    nouns: set[str] = set()
    adjs: set[str] = set()
    for name in names:
        for raw in re.split(r"[\s\-]+", clean_name(name)):
            raw = raw.strip(".,;:!?«»\"'")
            if len(raw) < 3 or (raw.isupper() and len(raw) <= 4):   # инициалы и аббревиатуры (HR, GTP)
                continue
            w = raw.lower().replace("ё", "е")
            if w in _STOP:
                continue
            if _LAT.match(w):
                exact.add(re.escape(w))
                continue
            if not _CYR.match(w):
                continue
            exact.add(w)
            if w.endswith(("ский", "цкий", "ская", "цкая", "ской", "цкой")) or (
                    len(w) >= 5 and w.endswith(("ый", "ой", "ая", "яя"))):
                adjs.add(w[:-2])
                continue
            stem = w[:-1] if w[-1] in "аяоеыийуюь" else w
            stem = _FLEETING.get(w, stem)
            if len(stem) >= 2:
                nouns.add(stem)
    return exact, nouns, adjs


@lru_cache(maxsize=64)
def _names_re(names: tuple[str, ...]) -> re.Pattern | None:
    exact, nouns, adjs = _word_parts(names)
    alts = []
    if nouns:
        alts.append(f"(?:{_yo(_alt(nouns))})(?:{_yo(_alt(_NOUN_END))})?")
    if adjs:
        alts.append(f"(?:{_yo(_alt(adjs))})(?:{_yo(_alt(_ADJ_END))})")
    if exact:
        alts.append(f"(?:{_yo(_alt(exact))})")
    if not alts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.IGNORECASE)


def _key(names: list[str] | None) -> tuple[str, ...]:
    return tuple(sorted({" ".join(n.split()) for n in names or [] if n and n.strip()}))


def mask_with_classes(text: str, names: list[str] | None = None) -> tuple[str, set[str]]:
    """Маскирует и возвращает классы найденных данных (для llm_log): name, phone, email, document."""
    classes: set[str] = set()
    out = text or ""

    def sub(rx: re.Pattern, repl: str, cls: str) -> None:
        nonlocal out
        out, n = rx.subn(repl, out)
        if n:
            classes.add(cls)

    sub(_EMAIL_RE, EMAIL_LABEL, "email")
    sub(_CARD_RE, DOC_LABEL, "document")
    sub(_SNILS_RE, DOC_LABEL, "document")
    sub(_PHONE_RE, PHONE_LABEL, "phone")
    sub(_PASSPORT_RE, DOC_LABEL, "document")
    sub(_SERIES_RE, r"\1\2" + DOC_LABEL, "document")
    sub(_NUMBER_RE, r"\1\2" + DOC_LABEL, "document")
    sub(_LONG_RE, DOC_LABEL, "document")
    rx = _names_re(_key(names))
    if rx is not None:
        sub(rx, NAME_LABEL, "name")
    # склеиваем «[ФИО] [ФИО]» после замены частей
    out = re.sub(r"(\[ФИО\])(\s+\[ФИО\])+", NAME_LABEL, out)
    return out, classes


def mask(text: str, names: list[str] | None = None) -> str:
    """ФИО, телефоны, e-mail и номера документов → метки [ФИО], [ТЕЛЕФОН], [EMAIL], [НОМЕР]."""
    return mask_with_classes(text, names)[0]
