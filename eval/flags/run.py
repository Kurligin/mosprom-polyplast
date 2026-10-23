"""Оценка словаря флагов (app/signals/rules.py) на синтетике: точность и полнота по каждому флагу.

Запуск: python -m eval.flags.run  →  eval/flags/RESULTS.md

Данные (все синтетические):
- survey-synth.jsonl — 120 ответов опросов Ивана (ветка ideas/ivan, docs/ideas/07-survey-answers-synth.jsonl);
  метки: safety_issue → безопасность, toxicity → травля, intent_to_leave → намерение уйти;
  остальные метки (no_access, unclear_tasks, conflict_manager, overload, burnout…) — темы, не флаги;
- extra.jsonl — 12 дописанных примеров «угрозы» и «здоровье» и 4 трудных отрицательных.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from app.signals import rules

HERE = Path(__file__).resolve().parent
FILES = {"Иван, 120 ответов": HERE / "survey-synth.jsonl", "дописанные, 16": HERE / "extra.jsonl"}
LABEL = {"safety_issue": "safety", "toxicity": "bullying", "intent_to_leave": "intent_to_leave",
         "threat": "threats", "health": "health"}
NAMES = {"safety": "Безопасность и охрана труда", "bullying": "Травля и унижения", "threats": "Угрозы",
         "health": "Здоровье", "intent_to_leave": "Намерение уйти (жёлтый, по тексту)"}
RED = {"safety", "bullying", "threats", "health"}


def load() -> list[dict]:
    rows = []
    for src, path in FILES.items():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["src"] = src
                r["gold"] = {LABEL[f] for f in r.get("flags") or [] if f in LABEL}
                r["pred"] = {h.category for h in rules.detect(r["text"])}
                rows.append(r)
    return rows


def _pr(tp: int, fp: int, fn: int) -> tuple[str, str]:
    p = f"{tp / (tp + fp):.2f}" if tp + fp else "—"
    r = f"{tp / (tp + fn):.2f}" if tp + fn else "—"
    return p, r


def score(rows: list[dict]) -> dict[str, Counter]:
    out: dict[str, Counter] = {c: Counter() for c in NAMES}
    out["red"] = Counter()
    for r in rows:
        for c in NAMES:
            g, p = c in r["gold"], c in r["pred"]
            out[c]["tp" if g and p else "fp" if p else "fn" if g else "tn"] += 1
        g, p = bool(r["gold"] & RED), bool(r["pred"] & RED)
        out["red"]["tp" if g and p else "fp" if p else "fn" if g else "tn"] += 1
    return out


def report(rows: list[dict]) -> str:
    sc = score(rows)
    lines = [
        "# Флаги: точность и полнота словаря",
        "",
        (f"Прогон {date.today():%d.%m.%Y}, `python -m eval.flags.run`. Словарь — `config/flag_rules.yaml`, без LLM. "
        f"Данные синтетические: {len(rows)} ответов "
        f"({', '.join(f'{k}' for k in FILES)})."),
        "",
        "| Флаг | Примеров | Нашёл верно | Лишние | Пропуски | Точность | Полнота |",
        "|---|---|---|---|---|---|---|",
    ]
    for c, name in NAMES.items():
        k = sc[c]
        p, r = _pr(k["tp"], k["fp"], k["fn"])
        lines.append(f"| {name} | {k['tp'] + k['fn']} | {k['tp']} | {k['fp']} | {k['fn']} | {p} | {r} |")
    k = sc["red"]
    p, r = _pr(k["tp"], k["fp"], k["fn"])
    lines.append(f"| **Любой красный флаг** (ответ ушёл в HR) | {k['tp'] + k['fn']} | {k['tp']} | {k['fp']} | "
                 f"{k['fn']} | **{p}** | **{r}** |")
    lines += ["", "По источникам:", "", "| Данные | Флаг | Точность | Полнота |", "|---|---|---|---|"]
    for src in FILES:
        part = score([r for r in rows if r["src"] == src])
        for c, name in NAMES.items():
            k = part[c]
            if k["tp"] + k["fn"] + k["fp"]:
                p, r = _pr(k["tp"], k["fp"], k["fn"])
                lines.append(f"| {src} | {name} | {p} | {r} |")

    misses = [r for r in rows if r["gold"] != r["pred"]]
    lines += ["", "## Расхождения", "", "| id | Метка | Словарь | Текст |", "|---|---|---|---|"]
    for r in misses:
        gold = ", ".join(sorted(r["gold"])) or "—"
        pred = ", ".join(sorted(r["pred"])) or "—"
        lines.append(f"| {r['id']} | {gold} | {pred} | {r['text'][:120]} |")
    lines += [
        "",
        "## Как читать",
        "",
        ("- **Красные флаги** (безопасность, травля, угрозы, здоровье) сразу создают обращение HR. Здесь важнее полнота: "
        "лишнее обращение HR закроет за минуту, пропуск опаснее."),
        ("- **Намерение уйти** по тексту ловится плохо и это ожидаемо: в синтетике оно почти всегда скрыто "
        "(«зарплата меньше, чем обещали», «сомневаюсь из-за дороги»). Прямой сигнал даёт кнопка опроса "
        "«Нет, задумываюсь об уходе» — она всегда создаёт красный флаг и обращение. Скрытые случаи — задача ИИ-разбора "
        "(тема и настроение, при согласии новичка), не словаря."),
        ("- Словарь составлялся с оглядкой на эти же 120 ответов, поэтому цифры по ним оптимистичны. "
        "Дописанные примеры «угрозы» и «здоровье» сначала прогнали вслепую: здоровье 6 из 6, угрозы 3 из 6 и одна лишняя "
        "тревога на «Угроз никаких нет». После этого в словарь добавили общие формы, а не фразы из примеров: "
        "инфинитивы («побить»), «не жить», обратный порядок слов («сломают ноги»), отрицание после слова («… нет»)."),
        "- Нужна проверка на живых ответах пилота: это первое, что стоит сделать после запуска.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = load()
    text = report(rows)
    (HERE / "RESULTS.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
