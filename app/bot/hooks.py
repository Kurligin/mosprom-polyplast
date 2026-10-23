"""Точки расширения бота. Модули регистрируют обработчики здесь и не трогают handlers.py.

Соглашения:
- payload кнопки: "<префикс>:<остальное>". Встроенные префиксы: ob (знакомство), hr.
  Модули: sc (сценарий), sv (опросы), kb (база знаний), tk (обращения), mg (руководитель).
- dialog_state: "<префикс>:<детали>", например "sv:12:3". Обработчик состояния выбирается по префиксу.
- Бот не флудит. Ответ на кнопку меняет то же сообщение (cb.replace / cb.mark),
  новое сообщение — только для нового повода (шаг сценария, ответ HR, ответ на вопрос).

Регистрация (в app/<модуль>/hooks.py, модуль подключается из app/plugins.py):

    from app.bot import hooks

    @hooks.on_callback("sv")
    async def survey_button(cb: hooks.Callback, person) -> None: ...

    @hooks.on_state("sv")
    async def survey_text(ctx, repo, emp, text: str, msg: dict) -> None: ...
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Callback:
    """Нажатие inline-кнопки. Ответить нужно ровно один раз: replace, mark или toast.

    Если обработчик не ответил, диспетчер сам вызовет mark() (или toast, если подписи кнопки нет).
    """

    ctx: Any
    repo: Any
    callback_id: str | None
    payload: str
    label: str | None        # подпись нажатой кнопки (из клавиатуры исходного сообщения)
    orig_text: str           # текст сообщения с кнопкой
    mid: str | None          # id сообщения с кнопкой
    chat_id: int | None
    user_id: int | None
    is_staff: bool = False
    answered: bool = False

    async def replace(self, text: str, attachments: list[dict] | None = None, *,
                      notification: str | None = None, employee=None, staff=None,
                      fmt: str | None = "markdown") -> None:
        """Заменить сообщение с кнопкой: новый текст и новая клавиатура (по умолчанию — без кнопок).

        Главный способ не флудить: следующий шаг диалога показывается в том же сообщении.
        fmt=None — без разметки: в тексте чужие слова (рассказ новичка), * и _ показываем как есть.
        """
        message = {"text": text, "attachments": attachments or []}
        if fmt:
            message["format"] = fmt
        await self._answer(message, notification)
        if self.repo is not None:
            await self.repo.add_message(employee_id=getattr(employee, "id", None),
                                        staff_id=getattr(staff, "id", None), chat_id=self.chat_id,
                                        direction="out", text=text, mid=self.mid)

    async def mark(self, extra: str | None = None) -> None:
        """Оставить текст, убрать клавиатуру и дописать отметку выбора «✅ <подпись>»."""
        choice = extra or self.label
        text = self.orig_text
        if choice:
            text = f"{text}\n\n✅ {choice}" if text else f"✅ {choice}"
        await self._answer({"text": text, "attachments": [], "format": "markdown"}, None)

    async def toast(self, text: str) -> None:
        """Всплывающее уведомление, сообщение не меняется."""
        await self._answer(None, text)

    async def _answer(self, message: dict | None, notification: str | None) -> None:
        if self.answered:
            log.warning("Повторный ответ на callback %s пропущен", self.payload)
            return
        self.answered = True
        if self.callback_id and self.ctx is not None:
            await self.ctx.sender.answer(self.callback_id, message=message, notification=notification,
                                         key=self.chat_id)


CallbackHandler = Callable[[Callback, Any], Awaitable[None]]
StateHandler = Callable[[Any, Any, Any, str, dict], Awaitable[None]]
CommandHandler = Callable[[Any, Any, Any, str], Awaitable[None]]
PreRoute = Callable[[Any, Any, Any, str, dict], Awaitable[bool]]
EmployeeHook = Callable[[Any, Any, Any], Awaitable[None]]

_callbacks: dict[str, CallbackHandler] = {}
_states: dict[str, StateHandler] = {}
_commands: dict[str, CommandHandler] = {}
_pre_routes: list[PreRoute] = []
_single: dict[str, Callable] = {}


def on_callback(prefix: str):
    """Кнопки с payload "<prefix>:…". Обработчик (cb, person): person — Employee или Staff (cb.is_staff)."""
    def deco(fn: CallbackHandler) -> CallbackHandler:
        _callbacks[prefix] = fn
        return fn
    return deco


def on_state(prefix: str):
    """Текст (или вложение) новичка, когда dialog_state начинается с "<prefix>:" или равен prefix.

    Обработчик (ctx, repo, emp, text, msg): msg — тело сообщения «МАКС» (body) с attachments.
    Вернул False — текст не про это состояние: бот ведёт его обычным маршрутом (pre_route, база знаний).
    """
    def deco(fn: StateHandler) -> StateHandler:
        _states[prefix] = fn
        return fn
    return deco


def on_command(name: str):
    """Команда новичка "/name arg". Обработчик (ctx, repo, emp, arg)."""
    def deco(fn: CommandHandler) -> CommandHandler:
        _commands[name.lstrip("/").lower()] = fn
        return fn
    return deco


def pre_route(fn: PreRoute) -> PreRoute:
    """Свободный текст до базы знаний (например, ответ в открытое обращение). True — текст обработан."""
    _pre_routes.append(fn)
    return fn


def pre_route_always(fn: PreRoute) -> PreRoute:
    """pre_route, который проверяет текст до состояний диалога (/hr, рассказ о себе, знакомство).

    Так красный флаг срабатывает в любом состоянии. Пока опрос ждёт текстовый ответ, бот его не зовёт:
    опрос сам проверяет флаги и привязывает их к ответу. В обычном маршруте он идёт по порядку загрузки.
    """
    fn.always = True
    _pre_routes.append(fn)
    return fn


def on_free_text(fn):
    """Свободный вопрос новичка → база знаний. Обработчик (ctx, repo, emp, text)."""
    _single["free_text"] = fn
    return fn


def on_onboarded(fn: EmployeeHook) -> EmployeeHook:
    """Новичок закончил знакомство (согласия и «ты/вы»). Обработчик (ctx, repo, emp)."""
    _single["onboarded"] = fn
    return fn


def on_create_ticket(fn):
    """Фабрика обращений: (ctx, repo, emp, text, *, source, category=None, is_personal=False) -> Ticket.

    Должна создать обращение и уведомить HR. Сообщение новичку пишет вызывающий код.
    """
    _single["create_ticket"] = fn
    return fn


def on_staff_text(fn):
    """Текст от сотрудника (HR, руководитель). Обработчик (ctx, repo, staff, text, msg)."""
    _single["staff_text"] = fn
    return fn


# --- доступ для handlers.py ---

def callback_handler(prefix: str) -> CallbackHandler | None:
    return _callbacks.get(prefix)


def state_handler(state: str | None) -> StateHandler | None:
    if not state:
        return None
    return _states.get(state.split(":", 1)[0])


def command_handler(name: str) -> CommandHandler | None:
    return _commands.get(name)


def pre_routes() -> list[PreRoute]:
    return list(_pre_routes)


def single(name: str) -> Callable | None:
    return _single.get(name)


def reset() -> None:
    """Для тестов."""
    _callbacks.clear()
    _states.clear()
    _commands.clear()
    _pre_routes.clear()
    _single.clear()
