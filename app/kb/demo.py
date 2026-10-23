"""Мини-демо поиска без БД: BM25 в памяти → топ-5 → GigaChat выбирает пункт → карточка.

Запуск (в Docker, из корня репо):
  python -m app.kb.demo "сколько длится обед" "когда платят зарплату"
  python -m app.kb.demo --golden 5        # первые 5 вопросов эталона
  python -m app.kb.demo --no-llm "…"      # только поиск
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
from pathlib import Path

from app.kb.card import render_card
from app.kb.chunker import Chunk, load_all_docs
from app.kb.query import STOP_WORDS, expand_synonyms, stem, tokenize
from app.kb.route import is_personal_hr_case

ROOT = Path(__file__).resolve().parents[2]


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.docs = [self._terms(f"{c.path} {c.text}") for c in chunks]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        self.tf = [Counter(d) for d in self.docs]
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [stem(w) for w in tokenize(text) if w not in STOP_WORDS and len(w) > 1]

    def search(self, query: str, top: int = 5) -> list[tuple[int, float]]:
        q = set(self._terms(query))
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scores.append((i, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top]


def card(chunk: Chunk, question: str = "") -> str:
    return render_card(chunk.text, chunk.source_label, question)


async def run(questions: list[str], use_llm: bool) -> None:
    chunks = load_all_docs(ROOT / "docs")
    index = BM25(chunks)
    print(f"Фрагментов в базе: {len(chunks)}\n")
    for q in questions:
        print("=" * 72)
        print(f"ВОПРОС: {q}")
        if is_personal_hr_case(q):
            print("→ личный кадровый случай: передаю HR (без поиска и LLM)\n")
            continue
        hits = index.search(expand_synonyms(q), top=5)
        if not hits:
            print("→ ничего не найдено: «не знаю, передал HR»\n")
            continue
        for rank, (i, s) in enumerate(hits, 1):
            c = chunks[i]
            kt = " [КТ]" if c.is_kt else ""
            print(f"  {rank}. {s:5.2f}  {c.source_label}{kt}")
        if not use_llm:
            print()
            continue
        from app.llm.gigachat import choose_chunk

        cands = [{"chunk_id": i, "text": chunks[i].text, "is_kt": chunks[i].is_kt} for i, _ in hits]
        print()
        if all(c["is_kt"] for c in cands):
            # КТ в LLM не уходит: в боте такой вопрос отвечается только по высокому порогу поиска.
            # В демо показываем лучший пункт поиска с пометкой.
            print("(все кандидаты с пометкой КТ — в LLM не отправлены, ответ по лучшему совпадению поиска)")
            print("КОРСИК: " + card(chunks[hits[0][0]], q) + "\n")
            continue
        chosen = await choose_chunk(q, cands)
        if chosen is None:
            print("КОРСИК: Не нашёл в документах точного ответа — передал вопрос HR, ответ придёт сюда.\n")
        else:
            print("КОРСИК: " + card(chunks[chosen], q) + "\n")


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("questions", nargs="*")
    ap.add_argument("--golden", type=int, default=0, help="взять N вопросов из eval/golden.jsonl")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    qs = list(a.questions)
    if a.golden:
        rows = [json.loads(x) for x in (ROOT / "eval" / "golden.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        qs += [r["question"] for r in rows[: a.golden]]
    if not qs:
        qs = ["сколько длится обед"]
    asyncio.run(run(qs, use_llm=not a.no_llm))


if __name__ == "__main__":
    main()
