"""Модели БД (раздел 3 dev-plan.md).

Перечисления храним строками: так проще добавлять значения без миграций.
"""

import secrets
from datetime import UTC, date, datetime, time

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

EMBEDDING_DIM = 1024


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_code() -> str:
    """Код привязки/приглашения: 8 символов [A-Za-z0-9_-]."""
    return secrets.token_urlsafe(6)


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


# --- Площадки и люди ---------------------------------------------------------


class Site(Base):
    __tablename__ = "sites"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)  # GrPP / Luzhniki / GTP / новые
    name: Mapped[str] = mapped_column(String(200))
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", server_default="Europe/Moscow")


class Staff(Base):
    """Персонал: HR, рекрутер, руководитель, руководитель HR (head_recruitment)."""

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32))  # hr | recruiter | manager | head_recruitment
    site_code: Mapped[str | None] = mapped_column(ForeignKey("sites.code"), nullable=True)
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bind_code: Mapped[str] = mapped_column(String(32), unique=True, default=new_code)
    created_at: Mapped[datetime] = created_at_col()

    site: Mapped[Site | None] = relationship()


class Employee(Base):
    """Новичок."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    first_name: Mapped[str] = mapped_column(String(100))
    site_code: Mapped[str] = mapped_column(ForeignKey("sites.code"), index=True)
    work_type: Mapped[str] = mapped_column(String(16), default="office", server_default="office")  # office | production
    start_date: Mapped[date] = mapped_column(Date)
    probation_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    recruiter_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    address_form: Mapped[str | None] = mapped_column(String(4), nullable=True)  # ty | vy | None — ещё не выбрал
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, default=new_code)
    status: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    # waiting | active | stopped | finished
    dialog_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dialog_data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    pending_question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # без FK: смысл задаёт бот/опросы
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    time_shift_hours: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # машина времени
    created_at: Mapped[datetime] = created_at_col()

    site: Mapped[Site] = relationship()
    manager: Mapped[Staff | None] = relationship(foreign_keys=[manager_id])
    recruiter: Mapped[Staff | None] = relationship(foreign_keys=[recruiter_id])


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))  # adaptation | ai_analysis
    given: Mapped[bool] = mapped_column(Boolean)
    given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    text_version: Mapped[str] = mapped_column(String(32), default="v1", server_default="v1")


# --- Сценарий ---------------------------------------------------------------


class ScenarioStep(Base):
    """Шаг сценария (импорт из xlsx)."""

    __tablename__ = "scenario_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # стабильный ключ шага для идемпотентности
    step_no: Mapped[str | None] = mapped_column(String(16), nullable=True)  # номер из xlsx («1.5»)
    order_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # порядок строк
    site_tags: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")  # пусто — всем площадкам
    offset_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # от даты выхода; < 0 — до выхода
    offset_kind: Mapped[str] = mapped_column(String(16), default="calendar", server_default="calendar")
    # calendar | work
    send_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    work_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # office | production | None — всем
    kind: Mapped[str] = mapped_column(String(16), default="message", server_default="message")
    # message | survey | event
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)  # информационный повод
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    buttons: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")  # [{text, next_step}]
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    parent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # для шагов «по событию»
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("surveys.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = created_at_col()


class Delivery(Base):
    """Плановая отправка шага сотруднику."""

    __tablename__ = "deliveries"
    __table_args__ = (Index("ix_deliveries_status_scheduled", "status", "scheduled_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("scenario_steps.id", ondelete="SET NULL"), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="planned", server_default="planned")
    # planned | sent | failed | skipped | cancelled
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)  # шаг + тег + сотрудник
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_col()


# --- Опросы -----------------------------------------------------------------


class Survey(Base):
    __tablename__ = "surveys"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_col()


class SurveyQuestion(Base):
    __tablename__ = "survey_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id", ondelete="CASCADE"), index=True)
    order_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    kind: Mapped[str] = mapped_column(String(16))  # choice | yes_no | scale | multi | choice_other | open
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    reason_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ≤ порога — спросить «что мешает?»
    branches: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # вариант → следующий вопрос
    flag_triggers: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # вариант → флаг
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SurveyAnswer(Base):
    __tablename__ = "survey_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("survey_questions.id", ondelete="CASCADE"), index=True)
    value: Mapped[str | None] = mapped_column(String(255), nullable=True)  # выбранный вариант
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # шкала 1–10
    text: Mapped[str | None] = mapped_column(Text, nullable=True)  # свободный ответ
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mood: Mapped[str | None] = mapped_column(String(16), nullable=True)  # positive | neutral | negative
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_col()


# --- Переписка и обращения --------------------------------------------------


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # button | hr_command | unknown | personal | flag | survey
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new", index=True)
    # new | in_progress | closed
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = created_at_col()
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    group_mid: Mapped[str | None] = mapped_column(String(128), nullable=True)  # уведомление в HR-группе

    employee: Mapped[Employee] = relationship()
    assignee: Mapped[Staff | None] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(4))  # in | out
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    mid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Setting(Base):
    """Ключ-значение: chat_id HR-группы и прочее."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSONB, nullable=True)


# --- База знаний ------------------------------------------------------------


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(16))  # pdf | pptx | md | hr_answer
    source: Mapped[str | None] = mapped_column(String(500), nullable=True)  # путь к файлу/ссылка
    site_codes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")  # пусто — все площадки
    is_kt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # коммерческая тайна
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")  # draft | confirmed
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    chunks: Mapped[list["KbChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class HrAnswer(Base):
    """Ответ HR, отмеченный «в базу» (индексируется как фрагмент kb_chunks)."""

    __tablename__ = "hr_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    site_code: Mapped[str | None] = mapped_column(ForeignKey("sites.code"), nullable=True)  # None — все площадки
    author_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")  # draft | confirmed
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = created_at_col()


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index("ix_kb_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    hr_answer_id: Mapped[int | None] = mapped_column(
        ForeignKey("hr_answers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    path: Mapped[str] = mapped_column(Text, default="", server_default="")  # «раздел → пункт» / слайд
    point_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chunk_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # pvtr:6.6.1, ac:slide9
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)  # «ПВТР, п. 6.5.2, стр. 15»
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    is_kt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Если не задан явно — заполняется триггером: to_tsvector('russian', path || text)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    document: Mapped[KbDocument | None] = relationship(back_populates="chunks")


class QuestionLog(Base):
    __tablename__ = "questions_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("kb_chunks.id", ondelete="SET NULL"), nullable=True)
    route: Mapped[str] = mapped_column(String(16))  # answer | unknown | hr | personal
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = created_at_col()


# --- Сигналы, руководитель, журнал LLM --------------------------------------


class Flag(Base):
    __tablename__ = "flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(8))  # red | yellow
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # survey | button | llm | message
    answer_id: Mapped[int | None] = mapped_column(ForeignKey("survey_answers.id", ondelete="SET NULL"), nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")  # new | resolved
    created_at: Mapped[datetime] = created_at_col()


class ManagerTask(Base):
    __tablename__ = "manager_tasks"
    __table_args__ = (UniqueConstraint("manager_id", "employee_id", "day", "kind", name="uq_manager_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    day: Mapped[int] = mapped_column(Integer)  # день адаптации
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="meeting", server_default="meeting")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")  # pending | done
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mid: Mapped[str | None] = mapped_column(String(128), nullable=True)  # push руководителю (правка на месте)
    created_at: Mapped[datetime] = created_at_col()


class LlmLog(Base):
    """Журнал обращений к LLM — без текстов, только классы данных."""

    __tablename__ = "llm_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = created_at_col()
    call_type: Mapped[str] = mapped_column(String(32))  # choose_chunk | classify | survey_analysis | ...
    data_classes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
