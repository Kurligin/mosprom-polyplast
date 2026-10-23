"""Модели сценария «Корсика»: шаг, правило времени, кнопка, контекст новичка."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

TimingKind = Literal[
    "before_start",     # за N дней до выхода
    "workday",          # N-й рабочий день (1 = день выхода)
    "calendar_offset",  # через N месяцев/недель/дней от выхода
    "month_start",      # первый день M-го месяца работы
    "probation_end",    # последний день ИС (+ смещение в днях)
    "event",            # по событию / ответу / отмашке
]


class TimingRule(BaseModel):
    kind: TimingKind
    # before_start: за сколько дней (берём большее из «2-3»)
    days_before: int | None = None
    # workday: номер рабочего дня
    n: int | None = None
    # calendar_offset
    months: int = 0
    weeks: int = 0
    days: int = 0
    # month_start: номер месяца работы (2 = первый день второго месяца)
    month: int | None = None
    # probation_end: смещение в календарных днях (0 = последний день ИС)
    offset_days: int = 0
    # event: имя события — answer / free_question / survey_reminder
    event: str | None = None
    after_step: int | None = None  # для answer: шаг с вопросом
    answer: str | None = None      # для answer: подпись кнопки
    # время отправки «HH:MM»; None — по умолчанию (10:00)
    time: str | None = None
    # правило унаследовано от предыдущей строки (пустая ячейка времени)
    inherited: bool = False
    # правило взято из config/scenario_overrides.yaml
    override: bool = False


class Button(BaseModel):
    label: str
    next_step: int | None = None  # шаг-ответ; у вариантов по площадкам — первый из группы
    action: str | None = None     # set_address:ty / set_address:vy / branch / send_attachment
    reply: str | None = None      # ответ бота на нажатие (из «Если да: …» в ТЗ)


StepKind = Literal["message", "survey", "event"]


class StepSpec(BaseModel):
    number: int                      # порядковый номер строки сценария, 1..N
    code: str | None = None          # «№ п/п» из xlsx (1.5, 3.3 …), неуникален
    row: int | None = None           # строка в xlsx
    site_tags: list[str] = Field(default_factory=list)  # пусто — для всех площадок
    timing_raw: str | None = None
    timing: TimingRule | None = None
    office_only: bool = False
    kind: StepKind = "message"
    channel: Literal["bot", "email"] = "bot"
    topic: str | None = None         # «Информационный повод»
    layout: str | None = None        # «Форма/Макет»
    text: str = ""
    directives: list[str] = Field(default_factory=list)  # строки «# Приложить …» из текста
    buttons: list[Button] = Field(default_factory=list)
    attachment: str | None = None    # основной файл в assets/
    attachment_raw: str | None = None  # как в колонке «Сопроводительное изображение»
    extra_attachments: list[str] = Field(default_factory=list)
    required: bool = False
    gate: str | None = None          # recruiter_go / plan_ready — ждёт отмашки
    comment: str | None = None       # колонки H/J и мусор из колонки тега

    def applies_to(self, site_code: str, work_type: str) -> bool:
        """Шаг для площадки и типа работы новичка."""
        if self.site_tags and site_code not in self.site_tags:
            return False
        if self.office_only and work_type != "office":
            return False
        return True


class EmployeeCtx(BaseModel):
    start_date: date
    probation_end: date
    site_code: str
    work_type: Literal["office", "production"] = "office"
    tz: str = "Europe/Moscow"
    time_shift_hours: int = 0  # «машина времени» для демо


class PlannedStep(BaseModel):
    step_number: int
    due_at: datetime  # tz-aware
    gate: str | None = None
