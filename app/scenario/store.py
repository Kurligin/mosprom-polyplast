"""Сценарий в БД: импорт xlsx в `scenario_steps`, площадки из тегов, обратное чтение шагов.

CLI (`make import`): python -m app.scenario.store data/raw/korsik_scenario.xlsx

**Стабильный ключ шага** (`ScenarioStep.key`, на нём держится идемпотентность отправок):

    <момент>|<площадки>|<порядковый номер внутри момента и площадок>
    wd1@1200|GrPP+Luzhniki|1      — первый шаг «в 1-й рабочий день в 12:00» для GrPP и Luzhniki
    pre3@1000|all|2               — второй шаг «за 2-3 дня до выхода» для всех
    cal0m0w10d@1000|GrPP+Luzhniki|3
    ans.3f9c1a2b|GrPP|1           — ответ на кнопку (хэш ключа вопроса и подписи кнопки)

Почему так: номер строки сдвигается при вставке строки, текст HR правит, «№ п/п» неуникален и часто пуст.
Момент и площадки — то, что определяет, кому и когда уходит шаг, поэтому ключ переживает:
правку текста, картинки и кнопок, перестановку строк между моментами, вставку и удаление шагов
в других моментах. Ключ меняется, если у шага поменялись время или теги площадок (это уже другой шаг),
или если перед ним вставили шаг в тот же момент для тех же площадок. Тогда шаг считается новым.
Уже отправленное повторно не уйдёт, если момент прошёл: шаги старше суток не отправляются.

Шаги, которых нет в новом xlsx, не удаляются, а выключаются (`is_active = false`): на них ссылаются отправки.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScenarioStep, Site
from app.scenario.importer import parse_xlsx
from app.scenario.models import Button, StepSpec, TimingRule

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

# Шаги, которые покрывает знакомство в боте: описание бота и приветствие («0»), шаги 1.1–1.4
COVERED_CODES = {"0", "1.1", "1.2", "1.3", "1.4"}

_SITE_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
# «(наложить на слово "плейлист" гиперссылку на https://…)» — указание из ТЗ, превращаем в ссылку
_LINK_HINT_RE = re.compile(
    r"\s*\(\s*наложить на слово\s*[\"«“](?P<word>[^\"»”]+)[\"»”]\s*гиперссылку на\s*(?P<url>\S+?)\s*\)", re.IGNORECASE)


@dataclass
class ImportReport:
    """Итог импорта. Поля `steps … warnings` — контракт `POST /api/scenario/import` (miniapp/API.md)."""

    steps: int = 0
    by_site: dict[str, int] = field(default_factory=dict)
    surveys: int = 0
    attachments: int = 0
    new_sites: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    deactivated: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# --- ключ шага ---------------------------------------------------------------

def _hhmm(rule: TimingRule) -> str:
    return (rule.time or "10:00").replace(":", "")


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def anchor(rule: TimingRule | None, keys_by_number: dict[int, str], row: int | None = None) -> str:
    """Момент шага: правило времени в коротком виде."""
    if rule is None:
        return f"row{row}"
    k = rule.kind
    if k == "before_start":
        return f"pre{rule.days_before or 1}@{_hhmm(rule)}"
    if k == "workday":
        return f"wd{rule.n}@{_hhmm(rule)}"
    if k == "calendar_offset":
        return f"cal{rule.months}m{rule.weeks}w{rule.days}d@{_hhmm(rule)}"
    if k == "month_start":
        return f"mon{rule.month}@{_hhmm(rule)}"
    if k == "probation_end":
        return f"pe{rule.offset_days:+d}@{_hhmm(rule)}"
    if rule.event == "answer":
        question = keys_by_number.get(rule.after_step or 0, "?")
        return "ans." + _short_hash(question, (rule.answer or "").lower())
    return f"ev.{rule.event}"


def _tags_part(spec: StepSpec) -> str:
    part = "+".join(sorted(spec.site_tags)) or "all"
    if spec.office_only:
        part += "+office"
    if len(part) > 28:
        part = "t." + _short_hash(part)
    return part


def step_keys(specs: list[StepSpec]) -> dict[int, str]:
    """Ключи всех шагов: номер шага → ключ."""
    keys: dict[int, str] = {}
    counter: Counter[str] = Counter()
    for s in specs:
        group = f"{anchor(s.timing, keys, s.row)}|{_tags_part(s)}"
        counter[group] += 1
        keys[s.number] = f"{group}|{counter[group]}"
    return keys


# --- шаг → строка БД ---------------------------------------------------------

def _apply_link_hints(text: str) -> str:
    """«…плейлист… (наложить на слово "плейлист" гиперссылку на URL)» → «…[плейлист](URL)…»."""
    m = _LINK_HINT_RE.search(text)
    if not m:
        return text
    word, url = m.group("word").strip(), m.group("url").strip()
    text = (text[:m.start()] + text[m.end():]).strip()
    return text.replace(word, f"[{word}]({url})", 1)


# Короткие человеческие заголовки шагов для Mini App («Ближайшие шаги»): первый совпавший шаблон
# по тексту, поводу и макету шага. Тексты самих шагов не меняются.
TITLES: list[tuple[str, str]] = [
    (r"от лица всей команды поздравляем", "Приветственное письмо"),
    (r"вопрос 1: были ли вопросы при оформлении|итогам третьего рабочего дня", "Опрос 3-го дня"),
    (r"^чек|хотел узнать, как у тебя дела", "Чек второй недели"),
    (r"завершением месяца работы", "Месяц работы"),
    (r"как идет работа по адаптационному плану", "Как идёт адаптационный план"),
    (r"итогам двух месяцев|с каким настроением ты ежедневно", "Опрос двух месяцев"),
    (r"итогам испытательного срока|самым сложным в период адаптации", "Финальный опрос"),
    (r"уже завтра наступит твой первый рабочий день", "Ты готов к первому дню?"),
    (r"поздравляю с первым рабочим днем", "Поздравление с первым днём"),
    (r"первый рабочий день позади", "Итоги первого дня"),
    (r"списк\w* (необходимых )?документ|необходимых для оформления документов", "Документы для оформления"),
    (r"корпоративн\w* транспорт|как добраться до офиса", "Как добраться"),
    (r"интересными фактами", "Факты о компании"),
    (r"полноценное погружение", "До встречи перед выходом"),
    (r"карты территории|территория довольно большая", "Карта территории"),
    (r"карты этажей|офис довольно большой", "Карта офиса"),
    (r"необходимые доступы|все ли необходимое|корсик_доступы", "Есть ли все доступы?"),
    (r"где пообедать", "Где пообедать"),
    (r"справочник", "Справочник контактов"),
    (r"презентац", "Презентация о компании"),
    (r"представлю тебя коллегам", "Представление коллегам"),
    (r"адаптационный план", "Адаптационный план"),
    (r"спортивн\w* (зал|комплекс)", "Спортзал"),
    (r"предлагаем пройти тебе опрос", "Опрос 3-го дня"),
    (r"корпоративн\w* мероприят|фотографий с мероприятий|ближайших мероприятиях", "Корпоративные мероприятия"),
    (r"встреча с менеджером по подбору", "Напоминание о встрече"),
    (r"подборку фильмов", "Подборка фильмов"),
    (r"небольшой опрос.*двух месяцев", "Опрос двух месяцев"),
    (r"финишной прямой", "Финишная прямая"),
    (r"книги для саморазвития", "Подборка книг"),
    (r"окончанием испытательного|3 месяца пролетели", "Конец испытательного срока"),
    (r"плейлист", "Плейлист"),
    (r"пришло время нам с тобой попрощаться", "Прощание"),
    (r"открытк", "Открытка"),
    (r"спасибо за обратную связь|учтем твои рекомендации", "Спасибо за ответы"),
    (r"приятного аппетита", "Где пообедать"),
    (r"не прошел опрос", "Напоминание об опросе"),
    (r"слишком сложные", "Вопрос передан HR"),
    ((r"как мы будем общаться|обращаться к сотруднику|приятно с тобой познакомиться|меня зовут корсик|"
     r"что умеет этот бот|добро пожаловать"),
     "Знакомство с Корсиком"),
]


def title_for(spec: StepSpec) -> str:
    """Короткий заголовок шага: по шаблонам TITLES, иначе — повод из xlsx или первая строка текста."""
    if re.fullmatch(r"привет[^\n]{0,12}", (spec.text or "").strip().lower()):
        return ""  # одно приветствие: заголовок берём у соседнего шага
    hay = "\n".join(x for x in (spec.text, spec.topic, spec.layout, spec.attachment_raw) if x).lower()
    hay = hay.replace("ё", "е")
    for pattern, title in TITLES:
        if re.search(pattern, hay):
            return title
    if spec.topic:
        return re.sub(r"\s+", " ", spec.topic).split(". ")[0].strip().rstrip(".")
    first = re.sub(r"\s+", " ", (spec.text or "").strip().split("\n")[0]).strip()
    return first[:60] + ("…" if len(first) > 60 else "")


def titles(specs: list[StepSpec], keys: dict[int, str]) -> dict[int, str]:
    """Заголовки всех шагов: продолжение опроса — заголовок опроса, приветствие — заголовок соседа."""
    out: dict[int, str] = {}
    prev: StepSpec | None = None
    for s in specs:
        same = prev is not None and anchor(prev.timing, keys) == anchor(s.timing, keys) if s.timing else False
        if s.kind == "survey" and same and prev.kind == "survey":
            out[s.number] = out[prev.number]
        else:
            out[s.number] = title_for(s)
        prev = s
    for i, s in enumerate(specs):
        if out[s.number]:
            continue
        group = [x for x in specs if x.timing and s.timing and anchor(x.timing, keys) == anchor(s.timing, keys)]
        after = [out.get(x.number) or title_for(x) for x in specs[i + 1:] if x in group]
        before = [out[x.number] for x in reversed(specs[:i]) if x in group and out.get(x.number)]
        out[s.number] = next((t for t in after + before if t), "Сообщение Корсика")
    return out


# Вопрос-предложение без кнопок и без ответа в ТЗ («Рассказать подробнее о расписании?», «Интересно?»):
# человек не должен видеть вопрос, на который нельзя ответить. Убираем его при импорте, текст ТЗ — в extra.
_DANGLING_RE = re.compile(r"\s*(Рассказать[^.!?]*\?|Интересно\?)\s*$")


def _drop_dangling_question(text: str, has_buttons: bool) -> str:
    if has_buttons:
        return text
    return _DANGLING_RE.sub("", text).rstrip()


def _send_time(rule: TimingRule | None) -> time | None:
    if rule is None or rule.kind == "event":
        return None
    h, m = (rule.time or "10:00").split(":")
    return time(int(h), int(m))


def _offset(rule: TimingRule | None) -> tuple[int | None, str]:
    """offset_days / offset_kind — для обзора в БД. Точное правило — в extra.timing."""
    if rule is None:
        return None, "calendar"
    if rule.kind == "before_start":
        return -(rule.days_before or 1), "calendar"
    if rule.kind == "workday":
        return rule.n, "work"
    if rule.kind == "calendar_offset":
        return rule.months * 30 + rule.weeks * 7 + rule.days, "calendar"
    if rule.kind == "month_start":
        return ((rule.month or 1) - 1) * 30, "calendar"
    return None, "calendar"


def _is_covered(spec: StepSpec) -> bool:
    return bool(spec.code in COVERED_CODES and spec.timing and spec.timing.kind == "before_start")


def _after_survey(specs: list[StepSpec], keys: dict[int, str]) -> dict[int, str]:
    """Реплики после опроса в тот же момент («Спасибо за обратную связь!», плейлист, прощание).

    Их отправляем, когда новичок прошёл опрос, а не вместе с первым вопросом. Номер шага → ключ шага-опроса.
    """
    res: dict[int, str] = {}
    survey_key: str | None = None
    prev: StepSpec | None = None
    for s in specs:
        same_moment = prev is not None and prev.timing and s.timing and \
            anchor(prev.timing, keys) == anchor(s.timing, keys)
        if s.kind == "survey":
            if not (same_moment and survey_key):
                survey_key = keys[s.number]
        elif survey_key and same_moment and s.kind == "message" and not s.gate:
            res[s.number] = survey_key
        else:
            survey_key = None
        prev = s
    return res


def _buttons(spec: StepSpec, keys: dict[int, str]) -> list[dict]:
    out = []
    for b in spec.buttons:
        item = {"text": b.label, "next_step": b.next_step, "next_key": keys.get(b.next_step or 0),
                "action": b.action, "reply": b.reply}
        # «Есть ли у тебя все необходимые доступы?» → «Нет»: кнопка «Напомнить руководителю про доступы»
        if b.label.strip().lower() == "нет" and "доступ" in spec.text.lower():
            item["followup"] = "remind_access"
        out.append(item)
    return out


def row_values(spec: StepSpec, keys: dict[int, str], after_survey: dict[int, str],
               step_titles: dict[int, str] | None = None) -> dict:
    """Колонки ScenarioStep для шага."""
    rule = spec.timing
    offset_days, offset_kind = _offset(rule)
    parent = None
    if rule and rule.kind == "event" and rule.event == "answer" and rule.after_step:
        parent = keys.get(rule.after_step)
    extra = {
        "number": spec.number,
        "row": spec.row,
        "timing": rule.model_dump(mode="json") if rule else None,
        "timing_raw": spec.timing_raw,
        "office_only": spec.office_only,
        "channel": spec.channel,
        "gate": spec.gate,
        "layout": spec.layout,
        "directives": spec.directives,
        "attachment_raw": spec.attachment_raw,
        "extra_attachments": spec.extra_attachments,
        "comment": spec.comment,
        "covered": _is_covered(spec),
        "after_survey": after_survey.get(spec.number),
        "title": (step_titles or {}).get(spec.number) or title_for(spec),
        "text_tz": spec.text,
    }
    return {
        "step_no": spec.code,
        "order_no": spec.number,
        "site_tags": list(spec.site_tags),
        "offset_days": offset_days,
        "offset_kind": offset_kind,
        "send_time": _send_time(rule),
        "work_type": "office" if spec.office_only else None,
        "kind": spec.kind,
        "topic": spec.topic,
        "text": _drop_dangling_question(_apply_link_hints(spec.text), bool(spec.buttons)),
        "buttons": _buttons(spec, keys),
        "attachment": spec.attachment,
        "is_required": spec.required,
        "parent_key": parent,
        "is_active": True,
        "extra": extra,
    }


def spec_from_row(row: ScenarioStep) -> StepSpec:
    """Обратно: строка БД → StepSpec для расписания (schedule.plan_for / due_steps)."""
    ex = row.extra or {}
    timing = TimingRule(**ex["timing"]) if ex.get("timing") else None
    buttons = [Button(label=b.get("text") or "", next_step=b.get("next_step"), action=b.get("action"),
                      reply=b.get("reply")) for b in (row.buttons or [])]
    return StepSpec(
        number=row.order_no, code=row.step_no, row=ex.get("row"), site_tags=list(row.site_tags or []),
        timing_raw=ex.get("timing_raw"), timing=timing, office_only=bool(ex.get("office_only")),
        kind=row.kind if row.kind in ("message", "survey", "event") else "message",
        channel=ex.get("channel") or "bot", topic=row.topic, layout=ex.get("layout"), text=row.text or "",
        directives=ex.get("directives") or [], buttons=buttons, attachment=row.attachment,
        attachment_raw=ex.get("attachment_raw"), extra_attachments=ex.get("extra_attachments") or [],
        required=row.is_required, gate=ex.get("gate"), comment=ex.get("comment"),
    )


async def active_steps(session: AsyncSession) -> list[ScenarioStep]:
    return list((await session.scalars(
        select(ScenarioStep).where(ScenarioStep.is_active).order_by(ScenarioStep.order_no))).all())


# --- импорт -------------------------------------------------------------------

def _warnings(specs: list[StepSpec]) -> list[str]:
    out = []
    for s in specs:
        label = f"Шаг {s.number}" + (f" ({s.code})" if s.code else "") + (f", строка {s.row}" if s.row else "")
        if s.timing is None:
            out.append(f"{label}: не разобрано время «{s.timing_raw or ''}», шаг не отправится")
        for b in s.buttons:
            if b.action == "branch" and not b.next_step:
                out.append(f"{label}: у кнопки «{b.label}» нет шага-ответа")
        if s.attachment and not (ASSETS_DIR / s.attachment).exists():
            out.append(f"{label}: нет файла {s.attachment} в assets/, отправим макет")
    return out


async def import_specs(session: AsyncSession, specs: list[StepSpec]) -> ImportReport:
    keys = step_keys(specs)
    after = _after_survey(specs, keys)
    step_titles = titles(specs, keys)
    existing = {r.key: r for r in (await session.scalars(select(ScenarioStep))).all()}
    report = ImportReport(steps=len(specs), warnings=_warnings(specs))

    seen: set[str] = set()
    for spec in specs:
        key = keys[spec.number]
        values = row_values(spec, keys, after, step_titles)
        seen.add(key)
        row = existing.get(key)
        if row is None:
            session.add(ScenarioStep(key=key, **values))
            report.created += 1
        else:
            for k, v in values.items():
                setattr(row, k, v)
            report.updated += 1
    for key, row in existing.items():
        if key not in seen and row.is_active:
            row.is_active = False
            report.deactivated += 1
    await session.flush()

    by_site: Counter[str] = Counter()
    for s in specs:
        for t in s.site_tags or ["all"]:
            by_site[t] += 1
    report.by_site = dict(by_site)
    report.surveys = sum(s.kind == "survey" for s in specs)
    report.attachments = sum(bool(s.attachment) for s in specs)
    tags = {t for s in specs for t in s.site_tags}
    known = set((await session.scalars(select(Site.code))).all())
    report.new_sites = sorted(tags - known)
    return report


async def import_xlsx(session: AsyncSession, path: str | Path) -> ImportReport:
    """xlsx → upsert в scenario_steps. Коммит делает вызывающий код.

    ValueError — файл не открывается или в нём нет шагов: импорт пустого списка выключил бы весь сценарий.
    """
    try:
        specs = parse_xlsx(path)
    except ValueError:
        raise
    except Exception as e:  # битый архив: BadZipFile, InvalidFileException
        raise ValueError("файл не открывается: он повреждён или это не таблица Excel") from e
    if not specs:
        raise ValueError("в файле нет шагов сценария, загрузите таблицу сценария Корсика")
    return await import_specs(session, specs)


# --- площадки -------------------------------------------------------------------

async def pending_sites(session: AsyncSession) -> list[str]:
    """Теги площадок из активных шагов, которых нет в `sites`: HR должен задать название и часовой пояс."""
    tags = {t for row in await active_steps(session) for t in (row.site_tags or [])}
    known = set((await session.scalars(select(Site.code))).all())
    return sorted(tags - known)


async def confirm_site(session: AsyncSession, code: str, name: str, tz: str = "Europe/Moscow") -> Site:
    """Добавить или обновить площадку. ValueError — неверный код или часовой пояс."""
    code = (code or "").strip()
    if not _SITE_CODE_RE.match(code):
        raise ValueError("Код площадки — латиница и цифры, до 32 символов")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"Неизвестный часовой пояс: {tz}") from e
    site = await session.get(Site, code)
    if site is None:
        site = Site(code=code, name=(name or code).strip(), tz=tz)
        session.add(site)
    else:
        site.name = (name or site.name).strip()
        site.tz = tz
    await session.flush()
    return site


# --- загрузка вложений заранее -----------------------------------------------------

async def warm_uploads(session) -> list[str]:
    """Загрузить в «МАКС» все файлы шагов и закэшировать токены: в демо вложения уходят сразу.

    Работает отдельно от бота (только POST /uploads, без long polling). → имена загруженных файлов.
    """
    from app.bot import uploads
    from app.bot.api import MaxApi
    from app.bot.repo import SqlRepo
    from app.bot.sender import Sender
    from app.config import settings
    from app.scenario import mockups

    if not settings.max_bot_token:
        return []
    api = MaxApi(settings.max_bot_token, settings.max_api_base_url, settings.ca_bundle_file)
    sender, repo, done = Sender(api), SqlRepo(session), []
    names = sorted({r.attachment for r in await active_steps(session) if r.attachment} | {"plan_example.pdf"})
    try:
        for name in names:
            await uploads.attachment(mockups.ensure_asset(name), repo=repo, sender=sender)
            done.append(name)
    finally:
        await api.close()
    return done


# --- CLI ------------------------------------------------------------------------

async def _main(path: str, upload: bool = False) -> int:
    from app.db.session import async_session, engine

    async with async_session() as session:
        report = await import_xlsx(session, path)
        await session.commit()
        if upload:
            names = await warm_uploads(session)
            await session.commit()
            print(f"Загружено в «МАКС»: {len(names)} файлов")
    await engine.dispose()
    print(f"Шагов: {report.steps} (новых {report.created}, обновлено {report.updated}, "
          f"выключено {report.deactivated})")
    print("По площадкам: " + ", ".join(f"{k}={v}" for k, v in report.by_site.items()))
    print(f"Шагов-опросов: {report.surveys}; с вложениями: {report.attachments}")
    if report.new_sites:
        print("Новые площадки (задайте название и часовой пояс в Mini App): " + ", ".join(report.new_sites))
    for w in report.warnings:
        print("! " + w)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Импорт сценария Корсика из xlsx в БД")
    ap.add_argument("xlsx")
    ap.add_argument("--upload", action="store_true", help="заранее загрузить файлы шагов в «МАКС»")
    args = ap.parse_args(argv)
    return asyncio.run(_main(args.xlsx, args.upload))


if __name__ == "__main__":
    raise SystemExit(main())
