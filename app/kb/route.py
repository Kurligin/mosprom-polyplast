"""Маршрут вопроса: личный кадровый случай → HR; иначе решение по баллу поиска."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "hr_personal_rules.yaml"
THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "config" / "kb.yaml"
DEFAULT_HI, DEFAULT_LO = 0.62, 0.45

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
    return bool(first.search(t) and any(p.search(t) for p in topics))


def decide(best_score: float | None, hi: float, lo: float) -> Decision:
    """Порог ответа: ≥ hi — ответ сразу, [lo, hi) — спросить LLM, ниже — HR."""
    if best_score is None:
        return "escalate"
    if best_score >= hi:
        return "answer"
    if best_score >= lo:
        return "ask_llm"
    return "escalate"


@lru_cache(maxsize=1)
def kb_config(path: str | None = None) -> dict:
    p = Path(path or THRESHOLDS_PATH)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {}


def thresholds() -> tuple[float, float]:
    """(порог ответа сразу, порог вопроса к LLM) из config/kb.yaml."""
    data = kb_config()
    return float(data.get("answer_threshold", DEFAULT_HI)), float(data.get("llm_threshold", DEFAULT_LO))


def agree_direct() -> bool:
    """Отвечать без LLM, если оба поиска поставили пункт первым (config/kb.yaml)."""
    return bool(kb_config().get("agree_direct", True))


def plan(hits: list, hi: float, lo: float, *, agree_direct: bool = True) -> tuple[str, list]:
    """Что делать с найденным (без БД и LLM, общий код бота и оценки).

    → ("answer", [пункт]) | ("ask_llm", [кандидаты без КТ]) | ("escalate", []).
    Уверенность — лучшая косинусная близость в топе. Фрагменты КТ в LLM не уходят:
    если подходят только они, отвечаем, лишь когда оба поиска поставили пункт первым.
    agree_direct — пункт первый и в полнотекстовом, и в векторном поиске → ответ без LLM.
    """
    if not hits:
        return "escalate", []
    conf = max((h.sim or 0.0) for h in hits)
    top = hits[0]
    if conf >= hi:
        return "answer", [top if (top.sim or 0.0) >= hi else max(hits, key=lambda h: h.sim or 0.0)]
    if conf < lo:
        return "escalate", []
    agree = top.fts_rank == 1 and top.vec_rank == 1
    if agree and (agree_direct or top.is_kt):
        return "answer", [top]
    safe = [h for h in hits if not h.is_kt]
    if not safe:
        return "escalate", []
    return "ask_llm", safe
