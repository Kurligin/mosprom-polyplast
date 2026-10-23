"""Обработчики событий «МАКС». Состояние диалога — в Employee.dialog_state / dialog_data, не в памяти."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.bot import texts as T
from app.bot.keyboards import callback, keyboard, open_app

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# Состояния знакомства
S_CONSENT = "onboard_consent"
S_AI = "onboard_ai"
S_FORM = "onboard_form"
S_HR_QUESTION = "hr_question"
ONBOARDING = (S_CONSENT, S_AI, S_FORM)

# Подписи выбранных кнопок — для отметки после POST /answers
CHOICE_LABELS = {
    "consent:yes": "Согласен",
    "ai:yes": "Да",
    "ai:no": "Нет",
    "form:ty": T.FORM_TY,
    "form:vy": T.FORM_VY,
    "hr:forward": T.FORWARD_HR,
}


@dataclass
class BotCtx:
    sender: object           # app.bot.sender.Sender
    bot_name: str = ""       # публичное имя бота (для open_app)
    miniapp_url: str = ""


# --- клавиатуры ---

def kb_consent():
    return keyboard([callback("Согласен", "consent:yes"), callback("Что это значит?", "consent:info")])


def kb_consent_only():
    return keyboard([callback("Согласен", "consent:yes")])


def kb_ai():
    return keyboard([callback("Да", "ai:yes"), callback("Нет", "ai:no")])


def kb_form():
    return keyboard([callback(T.FORM_TY, "form:ty"), callback(T.FORM_VY, "form:vy")])


def kb_forward():
    return keyboard([callback(T.FORWARD_HR, "hr:forward")])


# --- вспомогательное ---

def _set_state(emp, state: str | None, **data) -> None:
    emp.dialog_state = state
    if data:
        # новый dict, чтобы SQLAlchemy заметил изменение JSONB
        emp.dialog_data = {**(emp.dialog_data or {}), **data}


async def _say(ctx: BotCtx, repo, person, text: str, attachments=None, *, is_staff: bool = False, ticket_id=None):
    if is_staff:
        return await ctx.sender.send(repo, text, chat_id=person.chat_id, user_id=person.max_user_id,
                                     attachments=attachments, staff=person, ticket_id=ticket_id)
    return await ctx.sender.send(repo, text, chat_id=person.chat_id, user_id=person.max_user_id,
                                 attachments=attachments, employee=person, ticket_id=ticket_id)


async def _notify_hr_group(ctx: BotCtx, repo, text: str) -> None:
    chat_id = await repo.get_setting("hr_group_chat_id")
    if chat_id:
        try:
            await ctx.sender.send(repo, text, chat_id=int(chat_id))
        except Exception as e:  # noqa: BLE001 — уведомление не должно ломать диалог
            log.warning("Не удалось уведомить HR-группу: %s", e)


async def _ask_current(ctx: BotCtx, repo, emp) -> None:
    """Повторить вопрос текущего шага знакомства."""
    if emp.dialog_state == S_CONSENT:
        await _say(ctx, repo, emp, T.WELCOME_CONSENT.format(name=emp.first_name), kb_consent())
    elif emp.dialog_state == S_AI:
        await _say(ctx, repo, emp, T.ASK_AI, kb_ai())
    elif emp.dialog_state == S_FORM:
        await _say(ctx, repo, emp, T.ASK_FORM, kb_form())


# --- привязка ---

async def bind_by_code(ctx: BotCtx, repo, code: str, user_id: int, chat_id: int | None) -> bool:
    """Ищем код у новичков, затем у персонала. True — код найден."""
    emp = await repo.employee_by_invite(code)
    if emp is not None:
        if emp.max_user_id and emp.max_user_id != user_id:
            await ctx.sender.send(repo, T.CODE_TAKEN, chat_id=chat_id, user_id=user_id)
            return True
        emp.max_user_id = user_id
        emp.chat_id = chat_id
        if emp.status == "stopped" and emp.address_form:
            emp.status = "active"
        if emp.status in ("waiting", "stopped") or not emp.address_form:
            _set_state(emp, S_CONSENT)
            await _ask_current(ctx, repo, emp)
        else:
            await _say(ctx, repo, emp, T.tv(emp, T.STOPPED_WELCOME_BACK))
        return True

    staff = await repo.staff_by_bind(code)
    if staff is not None:
        if staff.max_user_id and staff.max_user_id != user_id:
            await ctx.sender.send(repo, T.CODE_TAKEN, chat_id=chat_id, user_id=user_id)
            return True
        staff.max_user_id = user_id
        staff.chat_id = chat_id
        await greet_staff(ctx, repo, staff)
        return True
    return False


async def greet_staff(ctx: BotCtx, repo, staff) -> None:
    role = T.ROLE_NAMES.get(staff.role, staff.role)
    text = T.STAFF_BOUND.format(role=role) + "\n\n" + T.STAFF_HINT
    atts = None
    if ctx.miniapp_url and ctx.bot_name:
        atts = keyboard([open_app(T.OPEN_PANEL, ctx.bot_name.lstrip("@"))])
    await _say(ctx, repo, staff, text, atts, is_staff=True)


# --- шаг сценария (позже — движок scenario/) ---

async def send_welcome_step(ctx: BotCtx, repo, emp) -> None:
    """Заглушка движка сценария: первые сообщения шага «За 2-3 дня до выхода» (1.3, 1.4)."""
    await _say(ctx, repo, emp, T.STEP_1_3.replace("Х", emp.first_name))
    await _say(ctx, repo, emp, T.STEP_1_4)


# --- маршрут свободного текста (позже — kb/) ---

async def route_free_text(ctx: BotCtx, repo, emp, text: str) -> None:
    """Точка расширения: вопрос новичка → база знаний. Пока — предложение передать HR."""
    _set_state(emp, emp.dialog_state, last_question=text)
    await _say(ctx, repo, emp, T.tv(emp, T.FREE_TEXT_STUB), kb_forward())


async def create_hr_ticket(ctx: BotCtx, repo, emp, text: str, source: str) -> None:
    ticket = await repo.create_ticket(employee_id=emp.id, source=source, text=text,
                                      assignee_id=getattr(emp, "recruiter_id", None))
    await _say(ctx, repo, emp, T.tv(emp, T.HR_DONE), ticket_id=ticket.id)
    await _notify_hr_group(ctx, repo, T.HR_GROUP_TICKET.format(
        id=ticket.id, name=emp.full_name, site=emp.site_code, text=text))


# --- события ---

async def on_bot_started(ctx: BotCtx, repo, upd: dict) -> None:
    user_id = (upd.get("user") or {}).get("user_id")
    chat_id = upd.get("chat_id")
    payload = (upd.get("payload") or "").strip()
    await handle_start(ctx, repo, user_id, chat_id, payload)


async def handle_start(ctx: BotCtx, repo, user_id: int, chat_id: int | None, code: str) -> None:
    if code and CODE_RE.match(code):
        if await bind_by_code(ctx, repo, code, user_id, chat_id):
            return
        # неизвестный код, но пользователь уже привязан — просто продолжаем
        if not await _resume_bound(ctx, repo, user_id, chat_id):
            await ctx.sender.send(repo, T.CODE_NOT_FOUND, chat_id=chat_id, user_id=user_id)
        return
    if not await _resume_bound(ctx, repo, user_id, chat_id):
        await ctx.sender.send(repo, T.ASK_CODE, chat_id=chat_id, user_id=user_id)


async def _resume_bound(ctx: BotCtx, repo, user_id: int, chat_id: int | None) -> bool:
    """/start от уже привязанного пользователя: напомнить текущий шаг."""
    emp = await repo.employee_by_user(user_id)
    if emp is not None:
        if chat_id:
            emp.chat_id = chat_id
        if emp.status == "stopped":
            emp.status = "active" if emp.address_form else "waiting"
        if emp.dialog_state in ONBOARDING:
            await _ask_current(ctx, repo, emp)
        elif not emp.address_form:
            _set_state(emp, S_CONSENT)
            await _ask_current(ctx, repo, emp)
        else:
            await _say(ctx, repo, emp, T.tv(emp, T.STOPPED_WELCOME_BACK))
        return True
    staff = await repo.staff_by_user(user_id)
    if staff is not None:
        if chat_id:
            staff.chat_id = chat_id
        await greet_staff(ctx, repo, staff)
        return True
    return False


async def on_message_created(ctx: BotCtx, repo, upd: dict) -> None:
    msg = upd.get("message") or {}
    sender = msg.get("sender") or {}
    recipient = msg.get("recipient") or {}
    if sender.get("is_bot"):
        return
    if recipient.get("chat_type") not in (None, "dialog"):
        return  # в группах на сообщения не отвечаем
    user_id = sender.get("user_id")
    chat_id = recipient.get("chat_id")
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    mid = body.get("mid")

    emp = await repo.employee_by_user(user_id)
    staff = None if emp else await repo.staff_by_user(user_id)
    if emp is not None or staff is not None:
        await repo.add_message(employee_id=getattr(emp, "id", None), staff_id=getattr(staff, "id", None),
                               chat_id=chat_id, direction="in", text=text, mid=mid)

    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().split("@")[0]

    if cmd == "/start":
        await handle_start(ctx, repo, user_id, chat_id, arg.strip())
        return

    if emp is None and staff is None:
        # запасной вход: код текстом
        if text and CODE_RE.match(text):
            if await bind_by_code(ctx, repo, text, user_id, chat_id):
                return
            await ctx.sender.send(repo, T.CODE_NOT_FOUND, chat_id=chat_id, user_id=user_id)
            return
        await ctx.sender.send(repo, T.ASK_CODE, chat_id=chat_id, user_id=user_id)
        return

    if staff is not None:
        await greet_staff(ctx, repo, staff)
        return

    if chat_id and not emp.chat_id:
        emp.chat_id = chat_id

    if emp.dialog_state in ONBOARDING:
        await _say(ctx, repo, emp, T.PRESS_BUTTON)
        await _ask_current(ctx, repo, emp)
        return

    if cmd == "/hr":
        _set_state(emp, S_HR_QUESTION)
        await _say(ctx, repo, emp, T.tv(emp, T.HR_ASK))
        return
    if cmd == "/food":
        await _say(ctx, repo, emp, T.tv(emp, T.FOOD_STUB))
        return

    if emp.dialog_state == S_HR_QUESTION:
        if not text:
            await _say(ctx, repo, emp, T.tv(emp, T.HR_ASK))
            return
        _set_state(emp, None)
        await create_hr_ticket(ctx, repo, emp, text, source="hr_command")
        return

    if not text:
        return
    await route_free_text(ctx, repo, emp, text)


async def on_message_callback(ctx: BotCtx, repo, upd: dict) -> None:
    cb = upd.get("callback") or {}
    callback_id = cb.get("callback_id")
    payload = cb.get("payload") or ""
    user_id = (cb.get("user") or {}).get("user_id")
    msg = upd.get("message") or {}
    orig_text = (msg.get("body") or {}).get("text") or ""
    chat_id = (msg.get("recipient") or {}).get("chat_id")

    # закрываем нажатие: сообщение заменяется отметкой выбора, клавиатура убирается
    label = CHOICE_LABELS.get(payload)
    if callback_id:
        if label:
            new_text = f"{orig_text}\n\n✅ {label}" if orig_text else f"✅ {label}"
            await ctx.sender.answer(callback_id, message={"text": new_text, "attachments": [],
                                                          "format": "markdown"}, key=chat_id)
        elif payload == "consent:info":
            await ctx.sender.answer(callback_id, message={"text": orig_text, "attachments": [],
                                                          "format": "markdown"}, key=chat_id)
        else:
            await ctx.sender.answer(callback_id, notification="Принято", key=chat_id)

    emp = await repo.employee_by_user(user_id)
    if emp is None:
        return
    if chat_id and not emp.chat_id:
        emp.chat_id = chat_id
    await repo.add_message(employee_id=emp.id, staff_id=None, chat_id=chat_id or emp.chat_id,
                           direction="in", text=f"[кнопка] {label or payload}", mid=None)

    if payload == "consent:info" and emp.dialog_state == S_CONSENT:
        await _say(ctx, repo, emp, T.CONSENT_INFO, kb_consent_only())
    elif payload == "consent:yes" and emp.dialog_state == S_CONSENT:
        await repo.add_consent(emp.id, "adaptation", True, T.CONSENT_TEXT_VERSION)
        _set_state(emp, S_AI)
        await _ask_current(ctx, repo, emp)
    elif payload in ("ai:yes", "ai:no") and emp.dialog_state == S_AI:
        await repo.add_consent(emp.id, "ai_analysis", payload == "ai:yes", T.CONSENT_TEXT_VERSION)
        _set_state(emp, S_FORM)
        await _ask_current(ctx, repo, emp)
    elif payload in ("form:ty", "form:vy") and emp.dialog_state == S_FORM:
        emp.address_form = "ty" if payload == "form:ty" else "vy"
        emp.status = "active"
        _set_state(emp, None)
        await send_welcome_step(ctx, repo, emp)
    elif payload == "hr:forward":
        question = (emp.dialog_data or {}).get("last_question")
        if question:
            _set_state(emp, None, last_question=None)
            await create_hr_ticket(ctx, repo, emp, question, source="unknown")
        else:
            _set_state(emp, S_HR_QUESTION)
            await _say(ctx, repo, emp, T.tv(emp, T.HR_ASK))
    elif emp.dialog_state in ONBOARDING:
        # старая кнопка не из текущего шага — повторим актуальный вопрос
        await _ask_current(ctx, repo, emp)


async def on_bot_added(ctx: BotCtx, repo, upd: dict) -> None:
    if upd.get("is_channel"):
        return
    chat_id = upd.get("chat_id")
    if chat_id:
        await repo.set_setting("hr_group_chat_id", chat_id)
        log.info("HR-группа: chat_id сохранён")
        await ctx.sender.send(repo, T.HR_GROUP_HELLO, chat_id=chat_id)


async def on_bot_removed(ctx: BotCtx, repo, upd: dict) -> None:
    chat_id = upd.get("chat_id")
    if chat_id and await repo.get_setting("hr_group_chat_id") == chat_id:
        await repo.set_setting("hr_group_chat_id", None)


async def on_bot_stopped(ctx: BotCtx, repo, upd: dict) -> None:
    user_id = (upd.get("user") or {}).get("user_id")
    emp = await repo.employee_by_user(user_id)
    if emp is not None:
        emp.status = "stopped"
        await _notify_hr_group(ctx, repo, T.HR_GROUP_STOPPED.format(name=emp.full_name, site=emp.site_code))


HANDLERS = {
    "bot_started": on_bot_started,
    "message_created": on_message_created,
    "message_callback": on_message_callback,
    "bot_added": on_bot_added,
    "bot_removed": on_bot_removed,
    "bot_stopped": on_bot_stopped,
}


async def handle_update(ctx: BotCtx, repo, upd: dict) -> None:
    handler = HANDLERS.get(upd.get("update_type"))
    if handler is None:
        return
    await handler(ctx, repo, upd)
