"""Маршрут вопроса: личный кадровый случай → HR; иначе решение по баллу поиска."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "hr_personal_rules.yaml"

Decision = Literal["answer", "ask_llm", "escalate"]


@lru_cache(maxsize=1)
def _rules(path: str | None = None):
    data = yaml.safe_load(Path(path or RULES_PATH).read_text(encoding="utf-8")) or {}
    first = re.compile(r"(?<![а-яa-z])(" + "|".join(map(re.escape, data.get("first_person", []))) + r")(?![а-яa-z])")
    topics = [re.compile(p) for p in data.get("personal_topics", [])]
    always = [re.compile(p) for p in data.get("always", [])]
    return first, topics, always


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def is_personal_hr_case(text: str) -> bool:
    """Личный кадровый случай (зарплата не пришла, увольняют, заболел …) → сразу HR.

    Правило осторожное: при первом лице и любой «личной» теме — к HR.
    """
    t = _norm(text)
    first, topics, always = _rules()
    if any(p.search(t) for p in always):
        return True
    if first.search(t) and any(p.search(t) for p in topics):
        return True
    return False


def decide(best_score: float | None, hi: float, lo: float) -> Decision:
    """Порог ответа: ≥ hi — ответ сразу, [lo, hi) — спросить LLM, ниже — HR."""
    if best_score is None:
        return "escalate"
    if best_score >= hi:
        return "answer"
    if best_score >= lo:
        return "ask_llm"
    return "escalate"
