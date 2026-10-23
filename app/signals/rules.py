"""Словарь флагов: безопасность и охрана труда, травля, угрозы, здоровье, намерение уйти.

Правила из config/flag_rules.yaml работают всегда и без LLM: основы слов, фразы и пары «что + с чем»
в одном предложении. Файл перечитывается, если изменился.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "flag_rules.yaml"

# Порядок важности: в ответе новичку и в обращении первой идёт самая серьёзная тема
ORDER = ["threats", "health", "safety", "bullying", "intent_to_leave"]

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD = re.compile(r"[а-яa-z0-9-]+")


@dataclass
class Category:
    code: str
    topic: str
    level: str
    notice: bool
    emergency: bool
    free_text: bool
    stems: list[re.Pattern]
    patterns: list[re.Pattern]
    combos: list[tuple[list[re.Pattern], list[re.Pattern]]]


@dataclass
class Hit:
    category: str      # threats | health | safety | bullying | intent_to_leave
    topic: str         # тема по-русски для Flag.topic
    level: str         # red | yellow
    notice: bool
    emergency: bool
    quote: str         # предложение, где сработало правило (как написал новичок)
    match: str         # что именно совпало


@dataclass
class Rules:
    categories: list[Category]
    negations: set[str]
    personal: re.Pattern
    sensitive: list[re.Pattern]


def normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _rx(p: str, *, stem: bool = False) -> re.Pattern:
    return re.compile((r"\b(?:" + p + ")") if stem else p)


def parse(data: dict) -> Rules:
    cats: list[Category] = []
    for code, raw in (data.get("categories") or {}).items():
        cats.append(Category(
            code=code, topic=str(raw["topic"]), level=raw.get("level", "red"),
            notice=bool(raw.get("notice", True)), emergency=bool(raw.get("emergency", False)),
            free_text=bool(raw.get("free_text", False)),
            stems=[_rx(s, stem=True) for s in raw.get("stems") or []],
            patterns=[_rx(p) for p in raw.get("patterns") or []],
            combos=[([_rx(a) for a in c.get("any") or []], [_rx(w) for w in c.get("with") or []])
                    for c in raw.get("combos") or []],
        ))
    cats.sort(key=lambda c: ORDER.index(c.code) if c.code in ORDER else len(ORDER))
    return Rules(categories=cats, negations=set(data.get("negations") or []),
                 personal=re.compile(data.get("personal") or r"$^"),
                 sensitive=[re.compile(p) for p in data.get("sensitive") or []])


_cache: dict = {"mtime": None, "path": None, "rules": None}


def load(path: Path | str | None = None) -> Rules:
    p = Path(path or os.environ.get("FLAG_RULES_PATH") or RULES_PATH)
    mtime = p.stat().st_mtime
    if _cache["path"] != p or _cache["mtime"] != mtime:
        _cache.update(path=p, mtime=mtime, rules=parse(yaml.safe_load(p.read_text(encoding="utf-8")) or {}))
    return _cache["rules"]


def _negated(low: str, start: int, end: int, negations: set[str]) -> bool:
    """«не / ни / нет» за 1–2 слова до совпадения или «нет» сразу после («угроз никаких нет»)."""
    before = low[max(0, start - 40):start]
    before = re.split(r"[.,;:!?()«»\"\n]", before)[-1]
    if any(w in negations for w in _WORD.findall(before)[-2:]):
        return True
    after = re.split(r"[.,;:!?()«»\"\n]", low[end:end + 40])[0]
    return "нет" in _WORD.findall(after)[:3]


def _sentences(low: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(low) if s.strip()]


def _quote(original: str, low: str, start: int) -> str:
    """Предложение исходного текста, в котором нашлось совпадение (нормализация не меняет длину)."""
    left = max(low.rfind(ch, 0, start) for ch in ".!?\n")
    ends = [i for i in (low.find(ch, start) for ch in ".!?\n") if i != -1]
    right = min(ends) + 1 if ends else len(low)
    return original[left + 1:right].strip()[:300] or original.strip()[:300]


def _match_category(cat: Category, original: str, low: str, negations: set[str]):
    for rx in cat.stems:
        for m in rx.finditer(low):
            if not _negated(low, m.start(), m.end(), negations):
                return m.start(), m.group(0)
    for rx in cat.patterns:
        m = rx.search(low)
        if m:
            return m.start(), m.group(0)
    for any_rx, with_rx in cat.combos:
        offset = 0
        for sent in _sentences(low):
            pos = low.find(sent, offset)
            offset = pos + len(sent)
            a = next((m for rx in any_rx if (m := rx.search(sent))), None)
            if a is None:
                continue
            w = next((m for rx in with_rx if (m := rx.search(sent))), None)
            if w is not None:
                return pos + a.start(), f"{a.group(0)} + {w.group(0)}"
    return None


def detect(text: str, *, free_text: bool = False, rules: Rules | None = None) -> list[Hit]:
    """Флаги по тексту, по одному на тему, самые серьёзные первыми.

    free_text=True — свободное сообщение вне опроса: только темы с free_text и только если человек пишет о себе.
    """
    if not text or not text.strip():
        return []
    rules = rules or load()
    low = normalize(text)
    if free_text and not rules.personal.search(low):
        return []
    hits: list[Hit] = []
    for cat in rules.categories:
        if free_text and not cat.free_text:
            continue
        found = _match_category(cat, text, low, rules.negations)
        if found:
            start, match = found
            hits.append(Hit(category=cat.code, topic=cat.topic, level=cat.level, notice=cat.notice,
                            emergency=cat.emergency, quote=_quote(text, low, start), match=match))
    return hits


def is_sensitive(text: str, rules: Rules | None = None) -> bool:
    """Тема здоровья и близкие: такой текст не уходит в облачную нейросеть."""
    rules = rules or load()
    low = normalize(text)
    if any(rx.search(low) for rx in rules.sensitive):
        return True
    return any(h.category == "health" for h in detect(text, rules=rules))
