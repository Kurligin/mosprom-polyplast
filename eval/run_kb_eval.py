"""Быстрая оценка базы знаний на эталоне без БД и LLM (BM25 в памяти).

Запуск: PYTHONPATH=. python eval/run_kb_eval.py
Считает: перехват личных вопросов (escalate), ложные перехваты (answerable),
попадание верного источника в топ-1 и топ-5 для вопросов, источник которых уже в базе.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.kb.chunker import load_all_docs
from app.kb.demo import BM25
from app.kb.query import expand_synonyms
from app.kb.route import is_personal_hr_case

ROOT = Path(__file__).resolve().parents[1]

DOC_HINTS = {
    "ПВТР": "pvtr",
    "Антикоррупционная": "anticorruption-policy",
    "Положение о КТ": "trade-secret",
    "Политика качества": "quality-ecology-policy",
}


def _range(a: str, b: str | None) -> list[str]:
    """«6.6.1–6.6.3» → [6.6.1, 6.6.2, 6.6.3]; «8–9» → [8, 9]."""
    if not b:
        return [a]
    pa, pb = a.split("."), b.split(".")
    if pa[:-1] != pb[:-1]:
        return [a, b]
    pre = ".".join(pa[:-1])
    return [(pre + "." if pre else "") + str(i) for i in range(int(pa[-1]), int(pb[-1]) + 1)]


def expected_keys(ref: str) -> set[str]:
    keys: set[str] = set()
    for part in ref.split(";"):
        slug = next((s for h, s in DOC_HINTS.items() if h in part), None)
        if slug == "pvtr":
            for a, b in re.findall(r"(\d+\.\d+(?:\.\d+)?)(?:\s*[–-]\s*(\d+\.\d+(?:\.\d+)?))?", part):
                keys |= {f"pvtr:{x}" for x in _range(a, b)}
        elif slug:
            seg = re.sub(r"\[[^\]]*\]", "", part)
            for a, b in re.findall(r"(\d+)(?:\s*[–-]\s*(\d+))?", seg):
                keys |= {f"{slug}:{x}" for x in _range(a, b)}
    return keys


def main() -> None:
    chunks = load_all_docs(ROOT / "docs")
    index = BM25(chunks)
    rows = [json.loads(x) for x in (ROOT / "eval" / "golden.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]

    esc = [r for r in rows if r["category"] == "escalate"]
    ans = [r for r in rows if r["category"] == "answerable"]
    caught = [r for r in esc if is_personal_hr_case(r["question"])]
    false_pos = [r for r in ans if is_personal_hr_case(r["question"])]

    top1 = top5 = total = 0
    misses = []
    for r in ans:
        exp = expected_keys(r["source_ref"])
        if not exp:
            continue  # источник пока не в базе (сценарий ТЗ)
        total += 1
        hits = [chunks[i].key for i, _ in index.search(expand_synonyms(r["question"]), top=5)]
        if hits and hits[0] in exp:
            top1 += 1
        if exp & set(hits):
            top5 += 1
        else:
            misses.append((r["id"], r["question"], r["source_ref"], hits[:3]))

    print(f"Фрагментов: {len(chunks)}")
    print(f"Личные вопросы перехвачены: {len(caught)}/{len(esc)}")
    print(f"Ложные перехваты среди отвечаемых: {len(false_pos)}/{len(ans)}")
    for r in false_pos:
        print(f"   ложный: {r['id']} {r['question']}")
    for r in esc:
        if r not in caught:
            print(f"   пропущен: {r['id']} {r['question']}")
    print(f"Источник в базе: {total} вопросов; топ-1: {top1}/{total}, топ-5: {top5}/{total}")
    for m in misses:
        print(f"   мимо: {m[0]} {m[1]} | ждали {m[2]} | нашли {m[3]}")


if __name__ == "__main__":
    main()
