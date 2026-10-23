"""Импорт сценария «Корсика» из xlsx ТЗ в список StepSpec.

CLI: python -m app.scenario.importer data/raw/korsik_scenario.xlsx [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import openpyxl
import yaml

from app.scenario.models import Button, StepSpec, TimingRule
from app.scenario.timing import extract_time, is_office_only, is_time_only, parse_timing

OVERRIDES_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario_overrides.yaml"
DEFAULT_JSON = Path("data/raw/scenario.json")

# Колонки листа сценария (строка 2 — заголовки)
COL = {"code": 0, "timing": 1, "topic": 2, "tag": 3, "layout": 4, "text": 5,
       "required": 6, "comment": 7, "image": 8, "note": 9}
HEADER_ROW = 2
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _s(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _code(v) -> str | None:
    """«№ п/п»: Excel превратил «1.5» в дату 2023-01-05 → обратно «1.5»."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return f"{v.day}.{v.month}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return _s(v)


def _tags(raw: str | None) -> tuple[list[str], str | None]:
    """Теги площадок — латинские коды через запятую; прочее — в комментарий."""
    if not raw:
        return [], None
    parts = [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]
    tags = [p for p in parts if _TAG_RE.match(p)]
    rest = [p for p in parts if not _TAG_RE.match(p)]
    return tags, ("; ".join(rest) or None)


def _attachment(raw: str | None) -> str | None:
    """«Изображение 4» → img_04.png, «Изображение Открытка №1» → card_01.png, «ФОТО» → events_photos.jpg."""
    if not raw:
        return None
    m = re.search(r"открытка\s*№?\s*(\d+)", raw, re.I)
    if m:
        return f"card_{int(m.group(1)):02d}.png"
    m = re.search(r"изображени\w*\s*(\d+)", raw, re.I)
    if m:
        return f"img_{int(m.group(1)):02d}.png"
    if raw.strip().lower() == "фото":
        return "events_photos.jpg"
    slug = re.sub(r"\W+", "_", raw.lower()).strip("_")
    return f"{slug}.png"


def _split_text(text: str) -> tuple[str, list[str]]:
    """Строки «# Приложить …» — указания для разработки, не текст сообщения."""
    keep, directives = [], []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            directives.append(line.strip().lstrip("#").strip())
        else:
            keep.append(line)
    return "\n".join(keep).strip(), directives


def _is_survey(layout: str | None, text: str) -> bool:
    if layout and re.search(r"\bопрос", layout.lower()):
        return True
    if re.match(r"\s*(вопрос\s*\d|ответы:)", text, re.I):
        return True
    questions = re.findall(r"^\s*\d+[.)]\s+.+\?", text, re.M)
    scales = re.search(r"\(да/нет\)|шкала|\(1\s*-", text, re.I)
    return len(questions) >= 2 or bool(scales and questions)


def _options(text: str) -> tuple[str, list[str], str]:
    """Отделяет «Варианты ответов:» от текста. → (вопрос, варианты, хвост)."""
    m = re.search(r"Варианты ответов:\s*\n", text)
    if not m:
        return text, [], ""
    head, rest = text[: m.start()].strip(), text[m.end():]
    opts, tail_lines, in_opts = [], [], True
    for line in rest.splitlines():
        om = re.match(r"\s*(?:-|\d+\))\s*(.+)", line)
        if in_opts and om:
            opts.append(om.group(1).strip())
        elif in_opts and not line.strip() and not opts:
            continue
        else:
            in_opts = False
            tail_lines.append(line)
    return head, opts, "\n".join(tail_lines).strip()


def _branch_replies(tail: str, opts: list[str]) -> list[tuple[str | None, bool]]:
    """Хвост «Если да: … / Если ответили 1-2: …» → (ответ, переход к следующей реплике) на каждую кнопку."""
    res: list[tuple[str | None, bool]] = [(None, False)] * len(opts)
    branches: list[tuple[str, list[str]]] = []
    common: list[str] = []
    for para in re.split(r"\n\s*\n", tail):
        lines = [ln for ln in para.strip().splitlines() if ln.strip() and not ln.strip().startswith("+")]
        if not lines:
            continue
        m = re.match(r"\s*Если\s+(.+?):\s*(.*)", lines[0], re.I)
        if m:
            body = [m.group(2)] if m.group(2).strip() else []
            branches.append((m.group(1).strip().lower(), body + lines[1:]))
        elif branches:
            common.extend(lines)
    for cond, body in branches:
        nums = [int(x) for x in re.findall(r"\d+", cond)]
        if len(nums) == 2 and "-" in cond:
            nums = list(range(nums[0], nums[1] + 1))
        idx = [n - 1 for n in nums if 0 < n <= len(opts)]
        if not idx:
            word = cond.split()[-1]  # «да» / «нет»
            idx = [i for i, o in enumerate(opts) if o.lower().startswith(word)]
        text = "\n".join(body).strip()
        is_next = text.lower().startswith("следующая реплика")
        reply = None if is_next else "\n\n".join(x for x in (text, "\n".join(common)) if x) or None
        for i in idx:
            res[i] = (reply, is_next)
    return res


def _load_overrides(path: Path = OVERRIDES_PATH) -> dict[int, dict]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {int(k): v for k, v in (data.get("steps") or {}).items()}


def _same_anchor(a: TimingRule, b: TimingRule) -> bool:
    ignore = {"time", "inherited", "override"}
    return a.model_dump(exclude=ignore) == b.model_dump(exclude=ignore)


def parse_xlsx(path: str | Path, overrides_path: Path = OVERRIDES_PATH) -> list[StepSpec]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]  # лист сценария («27.02.2026»); «Лист1» — черновой дубль шага 8
    overrides = _load_overrides(overrides_path)

    steps: list[StepSpec] = []
    prev: StepSpec | None = None
    last_question: StepSpec | None = None

    for row_idx, row in enumerate(ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True), start=HEADER_ROW + 1):
        cells = list(row) + [None] * (10 - len(row))
        if all(_s(c) is None for c in cells):
            continue
        number = len(steps) + 1
        timing_raw = _s(cells[COL["timing"]])
        topic = _s(cells[COL["topic"]])
        layout = _s(cells[COL["layout"]])
        raw_text = str(cells[COL["text"]] or "").strip()
        tags, tag_comment = _tags(_s(cells[COL["tag"]]))
        comment_parts = [x for x in (tag_comment, _s(cells[COL["comment"]]), _s(cells[COL["note"]])) if x]
        image_raw = _s(cells[COL["image"]])

        text, directives = _split_text(raw_text)
        channel = "bot"
        if text.lower().startswith("отправить приветственное письмо"):
            channel = "email"
            text = text.split("\n", 1)[1].strip() if "\n" in text else ""

        # --- время ---
        office_only = is_office_only(timing_raw, topic)
        timing = parse_timing(timing_raw)
        if timing is None and is_time_only(timing_raw) and prev and prev.timing:
            timing = prev.timing.model_copy(update={"time": extract_time(timing_raw), "inherited": True})
        elif timing is None and not timing_raw and prev and prev.timing:
            timing = prev.timing.model_copy(update={"inherited": True})
            office_only = office_only or prev.office_only
        elif timing and timing.time is None and prev and prev.timing and _same_anchor(timing, prev.timing):
            timing = timing.model_copy(update={"time": prev.timing.time})
        if timing and timing.kind == "event" and timing.event == "answer" and last_question:
            timing = timing.model_copy(update={"after_step": last_question.number})

        # --- кнопки ---
        buttons: list[Button] = []
        head, opts, tail = _options(text)
        if opts:
            text = head
            for label, (reply, is_next) in zip(opts, _branch_replies(tail, opts)):
                low = label.lower()
                action = None
                if "на ты" in low:
                    action = "set_address:ty"
                elif "на вы" in low:
                    action = "set_address:vy"
                elif is_next:
                    action = "branch"  # next_step — после разбора всех шагов
                buttons.append(Button(label=label, action=action, reply=reply))
        # «1. Вариант: … 2. Вариант: …» — два варианта текста, берём первый
        if re.match(r"\s*1\.\s*Вариант:", text):
            v = re.split(r"\n\s*2\.\s*Вариант:", text, maxsplit=1)
            first = re.sub(r"^\s*1\.\s*Вариант:\s*", "", v[0]).strip()
            second, _, rest = (v[1] if len(v) > 1 else "").partition("\n")
            text = (first + ("\n" + rest if rest.strip() else "")).strip()
            comment_extra = "вариант 2: " + second.strip()
        else:
            comment_extra = None

        # --- вложения ---
        attachment = _attachment(image_raw)
        extra: list[str] = []
        joined = " ".join(directives).lower()
        site = tags[0] if len(tags) == 1 else (tags[0] if tags else "all")
        if "пдф" in joined and "заведен" in joined:
            extra = [attachment] if attachment else []
            attachment = f"food_{site}.pdf"
        elif topic and "карт" in topic.lower():
            extra = [attachment] if attachment else []
            attachment = f"map_{site}.pdf"
            buttons.append(Button(label="Покажи карту", action="send_attachment"))

        # --- отмашка ---
        gate = None
        if any("отмашк" in c.lower() for c in comment_parts):
            gate = "plan_ready" if "адаптационный план" in joined or "адаптационный план" in text.lower() else "recruiter_go"

        kind = "message"
        if timing and timing.kind == "event" and timing.event in ("free_question", "survey_reminder"):
            kind = "event"
        elif _is_survey(layout, raw_text):
            kind = "survey"

        step = StepSpec(
            number=number, code=_code(cells[COL["code"]]), row=row_idx,
            site_tags=tags, timing_raw=timing_raw, timing=timing, office_only=office_only,
            kind=kind, channel=channel, topic=topic, layout=layout, text=text,
            directives=directives, buttons=buttons, attachment=attachment,
            attachment_raw=image_raw, extra_attachments=extra,
            required=str(cells[COL["required"]] or "").strip().lower() == "да",
            gate=gate, comment=" | ".join(comment_parts + ([comment_extra] if comment_extra else [])) or None,
        )

        # --- поправки из YAML ---
        ov = overrides.get(number)
        if ov:
            if "timing" in ov:
                step.timing = TimingRule(**ov["timing"], override=True)
            for k in ("office_only", "gate", "kind", "site_tags"):
                if k in ov:
                    setattr(step, k, ov[k])

        if buttons and any(b.action == "branch" for b in buttons):
            last_question = step
        steps.append(step)
        prev = step

    # кнопка «Да» → первый шаг-ответ (варианты по площадкам идут подряд)
    for s in steps:
        for b in s.buttons:
            if b.action == "branch":
                target = next((t for t in steps if t.timing and t.timing.event == "answer"
                               and t.timing.after_step == s.number
                               and (t.timing.answer or "").lower() in b.label.lower()), None)
                b.next_step = target.number if target else None
    return steps


def summary(steps: list[StepSpec]) -> dict:
    by_site = Counter()
    for s in steps:
        for t in s.site_tags or ["(все)"]:
            by_site[t] += 1
    files = sorted({f for s in steps for f in ([s.attachment] if s.attachment else []) + s.extra_attachments})
    return {
        "steps": len(steps),
        "by_site": dict(by_site),
        "office_only": sum(s.office_only for s in steps),
        "surveys": sum(s.kind == "survey" for s in steps),
        "events": sum(s.timing is not None and s.timing.kind == "event" for s in steps),
        "gated": {s.number: s.gate for s in steps if s.gate},
        "with_attachments": sum(bool(s.attachment) for s in steps),
        "attachment_files": files,
        "timing_kinds": dict(Counter(s.timing.kind if s.timing else "НЕ РАЗОБРАНО" for s in steps)),
        "unparsed": [s.number for s in steps if s.timing is None],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Импорт сценария Корсика из xlsx")
    ap.add_argument("xlsx")
    ap.add_argument("--json", default=str(DEFAULT_JSON), help="куда сохранить шаги (JSON)")
    args = ap.parse_args(argv)

    steps = parse_xlsx(args.xlsx)
    sm = summary(steps)
    print(f"Шагов всего: {sm['steps']}")
    print("По площадкам: " + ", ".join(f"{k}={v}" for k, v in sm["by_site"].items()))
    print(f"Только для офиса: {sm['office_only']}")
    print(f"Опросов (шагов-вопросов): {sm['surveys']}")
    print(f"По событию: {sm['events']}; по отмашке: {sm['gated']}")
    print(f"С вложениями: {sm['with_attachments']}; файлы: {', '.join(sm['attachment_files'])}")
    print(f"Правила времени: {sm['timing_kinds']}")
    print(f"Неразобранных времён: {len(sm['unparsed'])} {sm['unparsed'] or ''}")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([s.model_dump(mode="json") for s in steps], ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Сохранено: {out}")
    return 0 if not sm["unparsed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
