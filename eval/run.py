"""Оценка базы знаний на эталоне через живой поиск в БД и маршрут вопроса.

Запуск (в Docker): python -m eval.run            (make eval)
  --llm        вопросы между порогами отправить в GigaChat (ответы кэшируются в eval/.llm_cache.json)
  --tune       подобрать пороги по сетке и напечатать лучшие
  --write      записать таблицу в eval/RESULTS.md
  --verbose    разбор каждого вопроса

Считает:
- верный источник в топ-1 и топ-5 (вопросы, источник которых есть в базе), рядом — BM25 в памяти;
- перехват личных вопросов правилами и ложные перехваты;
- лишние отказы (вопрос из базы ушёл HR) и ответы не по тому пункту;
- долю вопросов, на которые бот ответил сам, без HR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden.jsonl"
CACHE = ROOT / "eval" / ".llm_cache.json"
RESULTS = ROOT / "eval" / "RESULTS.md"

# Пояснения к разбору промахов в RESULTS.md (по id вопроса)
NOTES = {
    "g010": "правила личных случаев: «я заболел» сразу к HR, так задумано",
    "g024": "п. 6.5.1 (право работодателя запретить игры и чтение) — та же норма, что в 6.4.2",
    "g032": "кандидаты только из Положения о КТ: в LLM не отправляем, без согласия поисков — к HR",
    "g033": "кандидаты только из Положения о КТ: в LLM не отправляем, без согласия поисков — к HR",
    "g069": "в эталоне «частично»: п. 6.8.6 отсылает к локальному акту о командировках",
    "g073": "в эталоне «частично»: памятка о корпоративных мероприятиях без дат",
}

DOC_HINTS = {
    "ПВТР": "pvtr",
    "Антикоррупционная": "anticorruption-policy",
    "Положение о КТ": "trade-secret",
    "Политика качества": "quality-ecology-policy",
}
# Справочные статьи сценария: фраза в source_ref → ключ статьи
SCENARIO_HINTS = {
    "список документов": "scenario:documents",
    "справочник": "scenario:directory",
    "Доступы": "scenario:access",
    "третий рабочий день 11:00": "scenario:gym",
    "транспорт": "scenario:transport",
    "Где пообедать": "scenario:food",
    "первый день 15:00": "scenario:mission",
    "корпоративные мероприятия": "scenario:events",
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
    prev: str | None = None
    for part in ref.split(";"):
        slug = next((s for h, s in DOC_HINTS.items() if h in part), None)
        if slug is None and prev and re.match(r"\s*(п\.|слайд)", part):
            slug = prev  # «ПВТР п. 6.6.7, стр. 16; п. 6.6.15, стр. 17»
        prev = slug or prev
        if slug == "pvtr":
            for a, b in re.findall(r"(\d+\.\d+(?:\.\d+)?)(?:\s*[–-]\s*(\d+\.\d+(?:\.\d+)?))?", part):
                keys |= {f"pvtr:{x}" for x in _range(a, b)}
        elif slug:
            seg = re.sub(r"\[[^\]]*\]", "", part)
            for a, b in re.findall(r"(\d+)(?:\s*[–-]\s*(\d+))?", seg):
                keys |= {f"{slug}:{x}" for x in _range(a, b)}
        if "Сценарий" in part or "сценарий" in part:
            keys |= {k for h, k in SCENARIO_HINTS.items() if h in part}
    return keys


def hit_key(chunk_key: str | None) -> str:
    """scenario:documents@GTP → scenario:documents; pvtr:6.4.2/3 → pvtr:6.4.2."""
    k = (chunk_key or "").split("/", 1)[0]
    return k.split("@", 1)[0]


@dataclass
class Row:
    id: str
    question: str
    category: str
    site: str | None
    expected: set[str]
    personal: bool = False
    hits: list = field(default_factory=list)  # app.kb.search.Hit
    keys: list[str] = field(default_factory=list)
    llm: int | None = None  # chunk_id, выбранный GigaChat (None — «нет подходящего»)
    llm_asked: bool = False
    bm25: list[str] = field(default_factory=list)

    @property
    def top_sim(self) -> float | None:
        return max((h.sim or 0.0) for h in self.hits) if self.hits else None

    @property
    def has_source(self) -> bool:
        return self.category == "answerable" and bool(self.expected)


def load_rows() -> list[Row]:
    out = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.append(Row(id=r["id"], question=r["question"], category=r["category"],
                       site=None if r["site"] == "any" else r["site"],
                       expected=expected_keys(r["source_ref"]) if r["category"] == "answerable" else set()))
    return out


# --- маршрут при заданных порогах ---


def route(row: Row, hi: float, lo: float, agree: bool = True) -> tuple[str, str | None]:
    """→ (маршрут, ключ показанного пункта). Маршруты: personal | answer | llm | unknown."""
    from app.kb.route import plan

    if row.personal:
        return "personal", None
    action, hs = plan(row.hits, hi, lo, agree_direct=agree)
    if action == "answer":
        return "answer", hit_key(hs[0].chunk_key)
    if action == "ask_llm":
        chosen = next((h for h in hs if h.chunk_id == row.llm), None)
        if chosen is not None:
            return "llm", hit_key(chosen.chunk_key)
    return "unknown", None


@dataclass
class Metrics:
    n: int = 0
    with_source: int = 0
    top1: int = 0
    top5: int = 0
    bm25_top1: int = 0
    bm25_top5: int = 0
    docs_n: int = 0  # вопросы с источником в документах (как в прошлом раунде, без памяток сценария)
    docs_top5: int = 0
    docs_bm25_top5: int = 0
    esc_total: int = 0
    esc_caught: int = 0
    false_personal: int = 0
    answerable: int = 0
    answered_right: int = 0
    answered_wrong: int = 0
    refused: int = 0  # лишние отказы: вопрос с источником в базе ушёл HR
    oob_total: int = 0
    oob_refused: int = 0
    answered_total: int = 0
    by_llm: int = 0
    details: list = field(default_factory=list)


def evaluate(rows: list[Row], hi: float, lo: float, agree: bool = True) -> Metrics:
    m = Metrics(n=len(rows))
    for r in rows:
        rt, key = route(r, hi, lo, agree)
        if r.category == "escalate":
            m.esc_total += 1
            m.esc_caught += r.personal
        if r.category == "answerable":
            m.answerable += 1
            m.false_personal += r.personal
        if r.has_source:
            m.with_source += 1
            m.top1 += bool(r.keys and r.keys[0] in r.expected)
            m.top5 += bool(set(r.keys[:5]) & r.expected)
            m.bm25_top1 += bool(r.bm25 and r.bm25[0] in r.expected)
            m.bm25_top5 += bool(set(r.bm25[:5]) & r.expected)
            if any(not k.startswith("scenario:") for k in r.expected):
                m.docs_n += 1
                m.docs_top5 += bool(set(r.keys[:5]) & r.expected)
                m.docs_bm25_top5 += bool(set(r.bm25[:5]) & r.expected)
        answered = rt in ("answer", "llm")
        m.answered_total += answered
        m.by_llm += rt == "llm"
        if r.has_source:
            if answered and key in r.expected:
                m.answered_right += 1
            elif answered:
                m.answered_wrong += 1
            elif rt == "unknown":
                m.refused += 1
        if r.category == "out_of_kb":
            m.oob_total += 1
            m.oob_refused += not answered
        m.details.append((r, rt, key))
    return m


def objective(m: Metrics) -> float:
    # неверный ответ хуже честного «не знаю»: штраф ×2; ответ вне базы — тоже ошибка
    oob_answered = m.oob_total - m.oob_refused
    return m.answered_right - 2.0 * m.answered_wrong - 2.0 * oob_answered


# --- прогон ---


async def collect(rows: list[Row], use_llm: bool, verbose: bool) -> None:
    from app.db.session import async_session
    from app.kb.card import shown_text
    from app.kb.chunker import load_all_docs
    from app.kb.demo import BM25
    from app.kb.embed import embed
    from app.kb.query import expand_synonyms
    from app.kb.route import is_personal_hr_case
    from app.kb.search import search

    chunks = load_all_docs(ROOT / "docs")
    bm = BM25(chunks)
    vecs = await embed([r.question for r in rows])
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    # у локальной модели свой кэш: ответы GigaChat ей не подставляются
    from app.llm.gigachat import _model_name, _provider

    llm_tag = f"@{_provider()}:{_model_name()}" if _provider() != "gigachat" else ""

    async with async_session() as session:
        for r, v in zip(rows, vecs, strict=True):
            r.personal = is_personal_hr_case(r.question)
            hits = await search(session, r.question, site_code=r.site, limit=5, qvec=v)
            r.hits = hits
            r.keys = [hit_key(h.chunk_key) for h in hits]
            r.bm25 = [chunks[i].key for i, _ in bm.search(expand_synonyms(r.question), top=5)]
            safe = [h for h in hits if not h.is_kt]
            if use_llm and not r.personal and safe:
                ck = f"v4{llm_tag}|{r.question}|{','.join(str(h.chunk_id) for h in safe)}"
                if ck not in cache:
                    from app.llm.gigachat import choose_chunk

                    cands = [{"chunk_id": h.chunk_id, "text": shown_text(h.text, r.question, h.focus)} for h in safe]
                    cache[ck] = await choose_chunk(r.question, cands)
                r.llm, r.llm_asked = cache[ck], True
            if verbose:
                print(f"{r.id} [{r.category}] {r.question}")
                for h, k in zip(hits, r.keys, strict=True):
                    mark = "✓" if k in r.expected else " "
                    print(f"   {mark} {h.sim or 0:.3f} rrf={h.score:.4f} f={h.fts_rank} v={h.vec_rank} {h.chunk_key}"
                          f"{' [КТ]' if h.is_kt else ''}")
        await session.rollback()
    if use_llm:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def tune(rows: list[Row]) -> list[tuple[float, float, float, bool, Metrics]]:
    grid = [round(0.30 + 0.01 * i, 2) for i in range(56)]
    res = []
    for agree in (True, False):
        for hi in grid:
            for lo in grid:
                if lo > hi:
                    continue
                m = evaluate(rows, hi, lo, agree)
                res.append((objective(m), hi, lo, agree, m))
    res.sort(key=lambda x: (-x[0], -x[4].answered_total, -x[1]))
    return res


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "—"


def table(m: Metrics, hi: float, lo: float, llm: bool) -> str:
    oob_ans = m.oob_total - m.oob_refused
    lines = [
        "| Метрика | Значение |",
        "|---|---|",
        f"| Верный источник в топ-1 (гибридный поиск) | {pct(m.top1, m.with_source)} |",
        f"| Верный источник в топ-5 (гибридный поиск) | {pct(m.top5, m.with_source)} |",
        f"| То же у BM25 в памяти: топ-1 / топ-5 | {pct(m.bm25_top1, m.with_source)} / {pct(m.bm25_top5, m.with_source)} |",
        (f"| Только вопросы по документам: топ-5 гибрид / BM25 | {pct(m.docs_top5, m.docs_n)} / "
        f"{pct(m.docs_bm25_top5, m.docs_n)} |"),
        f"| Личные вопросы перехвачены правилами (к HR без ИИ) | {pct(m.esc_caught, m.esc_total)} |",
        f"| Ложные перехваты среди обычных вопросов | {pct(m.false_personal, m.answerable)} |",
        f"| Ответ по верному пункту (вопрос из базы) | {pct(m.answered_right, m.with_source)} |",
        f"| Ответ не по тому пункту | {pct(m.answered_wrong, m.with_source)} |",
        f"| Лишние отказы (вопрос из базы ушёл HR) | {pct(m.refused, m.with_source)} |",
        f"| Вопрос вне базы: честное «не знаю» | {pct(m.oob_refused, m.oob_total)} |",
        f"| Вопрос вне базы: ответил наугад | {pct(oob_ans, m.oob_total)} |",
        f"| Бот ответил сам, без HR (все вопросы) | {pct(m.answered_total, m.n)} |",
        f"| Из них выбор пункта GigaChat | {m.by_llm} |",
    ]
    note = (f"Пороги: ответ сразу при близости ≥ {hi:.2f}, GigaChat выбирает пункт при {lo:.2f}–{hi:.2f}, "
            f"ниже — «не знаю» и обращение к HR. GigaChat {'включён' if llm else 'выключен (между порогами — к HR)'}.")
    return "\n".join(lines) + "\n\n" + note


def main() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--hi", type=float)
    ap.add_argument("--lo", type=float)
    ap.add_argument("--agree", choices=["yes", "no"], help="ответ без LLM при согласии обоих поисков")
    a = ap.parse_args()

    from app.kb.route import agree_direct, thresholds

    hi, lo = thresholds()
    agree = agree_direct() if a.agree is None else a.agree == "yes"
    hi = a.hi if a.hi is not None else hi
    lo = a.lo if a.lo is not None else lo

    rows = load_rows()
    asyncio.run(collect(rows, a.llm, a.verbose))

    if a.tune:
        best = tune(rows)
        print("Лучшие пороги (цель = верные ответы − 2 × неверные − 2 × ответы вне базы):")
        for score, h, low, agree, m in best[:10]:
            print(f"  hi={h:.2f} lo={low:.2f} согласие={agree} цель={score:.0f} верно={m.answered_right} неверно={m.answered_wrong} "
                  f"вне базы={m.oob_total - m.oob_refused} отказы={m.refused} без HR={m.answered_total}")

    m = evaluate(rows, hi, lo, agree)
    print(table(m, hi, lo, a.llm))
    if a.verbose:
        print("\nРазбор:")
        for r, rt, key in m.details:
            ok = "✓" if key in r.expected else ("·" if not r.has_source else "✗")
            print(f"  {r.id} {r.category:10} {rt:8} {ok} sim={r.top_sim or 0:.3f} {key or ''} | {r.question}")
    if a.write:
        write_results(m, hi, lo, a.llm)


def write_results(m: Metrics, hi: float, lo: float, llm: bool) -> None:
    misses = [(r, rt, key) for r, rt, key in m.details
              if (r.has_source and key not in r.expected) or (r.category == "out_of_kb" and key)]
    body = [
        "# Качество базы знаний на эталоне",
        "",
        (f"Эталон — `eval/golden.jsonl`: {m.n} вопросов ({m.answerable} отвечаемых, {m.esc_total} личных "
         f"кадровых, {m.oob_total} вне базы). Источник отвечаемого вопроса есть в базе у {m.with_source} вопросов."),
        ("Прогон — `make eval` (`python -m eval.run --llm --write`): живой поиск в Postgres "
         "(полнотекстовый + bge-m3 + RRF, фильтр по площадке) и маршрут вопроса."),
        "",
        table(m, hi, lo, llm),
        "",
        "Пороги подобраны на этом же эталоне, отдельной проверочной выборки пока нет: цифры оптимистичные.",
        "",
    ]
    if misses:
        body += ["## Где бот не ответил верным пунктом", "",
                 "| Вопрос | Ждали | Маршрут | Показал | Комментарий |", "|---|---|---|---|---|"]
        for r, rt, key in misses:
            exp = ", ".join(sorted(r.expected)) or "вне базы"
            body.append(f"| {r.question} | {exp} | {rt} | {key or '—'} | {NOTES.get(r.id, '')} |")
        body.append("")
    RESULTS.write_text("\n".join(body), encoding="utf-8")
    print(f"\nЗаписано: {RESULTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
