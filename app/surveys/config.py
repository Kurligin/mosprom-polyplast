"""Опросы из config/surveys.yaml: разбор и проверка. Файл перечитывается, если изменился."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SURVEYS_PATH = Path(__file__).resolve().parents[2] / "config" / "surveys.yaml"

KINDS = {"choice", "yes_no", "scale", "multi", "choice_other", "open"}
ASKS = {"reason", "other", "why", "problem"}
ACTIONS = {
    # do: (подпись по умолчанию, что делает)
    "recruiter": "Связаться с рекрутером",
    "hr": "Связаться с HR",
    "forward": "Передать проблему в подбор",
    "forward_hr": "Передать проблему в HR",
    "remind_access": "Напомнить руководителю про доступы",
}
MOODS = {"positive", "neutral", "negative"}


@dataclass
class Action:
    do: str
    label: str


@dataclass
class Option:
    label: str
    reply: str | None = None
    ask: str | None = None
    actions: list[Action] = field(default_factory=list)
    flag: dict | None = None          # {level, topic, notice}
    mood: str | None = None
    sites: list[str] = field(default_factory=list)
    exclusive: bool = False

    def for_site(self, site: str | None) -> bool:
        return not self.sites or site in self.sites


@dataclass
class Question:
    key: str
    kind: str
    text: str
    step: int | None = None
    topic: str | None = None
    sites: list[str] = field(default_factory=list)
    legend: str | None = None
    reason_below: int | None = None
    options: list[Option] = field(default_factory=list)

    def for_site(self, site: str | None) -> bool:
        return not self.sites or site in self.sites

    def visible_options(self, site: str | None) -> list[Option]:
        return [o for o in self.options if o.for_site(site)]


@dataclass
class Survey:
    code: str
    title: str
    steps: list[int]
    questions: list[Question]
    intro: str | None = None
    outro: str | None = None          # None — своё «спасибо», "" — без завершающей фразы
    remind: bool = True
    image: str | None = None          # файл из assets/ на карточке опроса

    def visible(self, site: str | None) -> list[Question]:
        return [q for q in self.questions if q.for_site(site)]

    def question(self, key: str) -> Question | None:
        return next((q for q in self.questions if q.key == key), None)


class SurveyConfigError(ValueError):
    pass


def _action(raw) -> Action:
    if isinstance(raw, str):
        raw = {"do": raw}
    do = raw.get("do")
    if do not in ACTIONS:
        raise SurveyConfigError(f"Неизвестное действие {do!r}")
    return Action(do=do, label=str(raw.get("label") or ACTIONS[do]))


def _option(raw, where: str) -> Option:
    if isinstance(raw, str):
        raw = {"label": raw}
    label = str(raw.get("label") or "").strip()
    if not label:
        raise SurveyConfigError(f"{where}: вариант без подписи")
    ask = raw.get("ask")
    if ask is not None and ask not in ASKS:
        raise SurveyConfigError(f"{where}: ask={ask!r}")
    mood = raw.get("mood")
    if mood is not None and mood not in MOODS:
        raise SurveyConfigError(f"{where}: mood={mood!r}")
    flag = raw.get("flag")
    if flag is not None:
        if flag.get("level") not in ("red", "yellow") or not flag.get("topic"):
            raise SurveyConfigError(f"{where}: флаг без уровня или темы")
        flag = {"level": flag["level"], "topic": str(flag["topic"]), "notice": bool(flag.get("notice", True))}
    reply = raw.get("reply")
    return Option(label=label, reply=str(reply).strip() if reply else None, ask=ask,
                  actions=[_action(a) for a in raw.get("actions") or []], flag=flag, mood=mood,
                  sites=list(raw.get("sites") or []), exclusive=bool(raw.get("exclusive")))


def _question(raw, code: str) -> Question:
    key = raw.get("key")
    kind = raw.get("kind")
    where = f"{code}.{key}"
    if not key or kind not in KINDS:
        raise SurveyConfigError(f"{where}: нет ключа или неизвестный тип {kind!r}")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise SurveyConfigError(f"{where}: нет текста")
    options = [_option(o, where) for o in raw.get("options") or []]
    if kind == "scale":
        if options:
            raise SurveyConfigError(f"{where}: у шкалы варианты строятся сами")
    elif kind == "open":
        if options:
            raise SurveyConfigError(f"{where}: у открытого вопроса нет вариантов")
    elif not options:
        raise SurveyConfigError(f"{where}: нет вариантов")
    if kind == "choice_other" and not any(o.ask == "other" for o in options):
        raise SurveyConfigError(f"{where}: нужен вариант с ask: other")
    return Question(key=str(key), kind=kind, text=text, step=raw.get("step"), topic=raw.get("topic"),
                    sites=list(raw.get("sites") or []), legend=raw.get("legend"),
                    reason_below=raw.get("reason_below"), options=options)


def parse(data: dict) -> dict[str, Survey]:
    surveys: dict[str, Survey] = {}
    seen_steps: dict[int, str] = {}
    for raw in (data or {}).get("surveys") or []:
        code = raw.get("code")
        if not code or code in surveys:
            raise SurveyConfigError(f"Опрос без кода или повтор: {code!r}")
        if len(code) > 12 or ":" in code:
            raise SurveyConfigError(f"{code}: код длиннее 12 символов или с двоеточием")
        questions = [_question(q, code) for q in raw.get("questions") or []]
        keys = [q.key for q in questions]
        if not questions or len(keys) != len(set(keys)):
            raise SurveyConfigError(f"{code}: нет вопросов или повтор ключей")
        steps = [int(s) for s in raw.get("steps") or []]
        for s in steps:
            if s in seen_steps:
                raise SurveyConfigError(f"Шаг {s} уже в опросе {seen_steps[s]}")
            seen_steps[s] = code
        outro = raw.get("outro")
        surveys[code] = Survey(code=code, title=str(raw.get("title") or code), steps=steps, questions=questions,
                               intro=(str(raw["intro"]).strip() if raw.get("intro") else None),
                               outro=None if outro is None else str(outro).strip(),
                               remind=bool(raw.get("remind", True)), image=raw.get("image") or None)
    return surveys


_cache: dict = {"mtime": None, "path": None, "surveys": {}}


def load(path: Path | str | None = None) -> dict[str, Survey]:
    """Опросы из YAML. Перечитываем, если файл изменился (HR может править без перезапуска)."""
    p = Path(path or os.environ.get("SURVEYS_PATH") or SURVEYS_PATH)
    mtime = p.stat().st_mtime
    if _cache["path"] != p or _cache["mtime"] != mtime:
        surveys = parse(yaml.safe_load(p.read_text(encoding="utf-8")))
        _cache.update(path=p, mtime=mtime, surveys=surveys)
    return _cache["surveys"]


def get(code: str) -> Survey | None:
    return load().get(code)


def step_map() -> dict[int, str]:
    return {s: survey.code for survey in load().values() for s in survey.steps}
