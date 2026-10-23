"""Обработчики событий «МАКС». Состояние диалога — в Employee.dialog_state / dialog_data, не в памяти.

Бот не флудит: знакомство — одно сообщение, которое меняется на месте, ответ на кнопку меняет то же сообщение.
Модули (сценарий, опросы, база знаний, обращения, руководитель) подключаются через app.bot.hooks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.bot import hooks
from app.bot import texts as T
from app.bot.hooks import Callback
from app.bot.keyboards import callback, keyboard, open_app

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# Состояния знакомства: «ты/вы» → согласие → ИИ-разбор
S_FORM = "ob:form"
S_CONSENT = "ob:consent"
S_AI = "ob:ai"
S_HR_QUESTION = "hr:question"
ONBOARDING = (S_FORM, S_CONSENT, S_AI)

# Старые имена состояний (до 23.10) → новые
_LEGACY_STATES = {"onboard_form": S_FORM, "onboard_consent": S_CONSENT, "onboard_ai": S_AI,
                  "hr_question": S_HR_QUESTION}


@dataclass
class BotCtx:
    sender: object           # app.bot.sender.Sender
    bot_name: str = ""       # публичное имя бота (для open_app)
    miniapp_url: str = ""


# --- вспомогательное ---

def _state(emp) -> str | None:
    st = emp.dialog_state
    return _LEGACY_STATES.get(st, st)


def _set_state(emp, state: str | None, **data) -> None:
    emp.dialog_state = state
    if data:
        # новый dict, чтобы SQLAlchemy заметил изменение JSONB
        emp.dialog_data = {**(emp.dialog_data or {}), **data}


def _onboarded(emp) -> bool:
    return bool((emp.dialog_data or {}).get("onboarded")) or emp.status in ("active", "finished")


async def _say(ctx: BotCtx, repo, person, text: str, attachments=None, *, is_staff: bool = False,
               ticket_id=None):
    if is_staff:
        return await ctx.sender.send(repo, text, chat_id=person.chat_id, user_id=person.max_user_id,
                                     attachments=attachments, staff=person, ticket_id=ticket_id)
    return await ctx.sender.send(repo, text, chat_id=person.chat_id, user_id=person.max_user_id,
                                 attachments=attachments, employee=person, ticket_id=ticket_id)


async def _notify_hr_group(ctx: BotCtx, repo, text: str, attachments=None) -> None:
    chat_id = await repo.get_setting("hr_group_chat_id")
    if chat_id:
        try:
            await ctx.sender.send(repo, text, chat_id=int(chat_id), attachments=attachments)
        except Exception as e:  # noqa: BLE001 — уведомление не должно ломать диалог
            log.warning("Не удалось уведомить HR-группу: %s", e)


def _sync_test_name(emp, user: dict | None) -> None:
    """Тестовому аккаунту берём имя из профиля «МАКС», а не из демо-карточки."""
    if not getattr(emp, "is_test", False) or not user:
        return
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    if not first:
        first = (user.get("name") or "").strip().split(" ")[0]
    if first:
        emp.first_name = first
        emp.full_name = f"{first} {last}".strip()


# --- знакомство ---

def onboarding_view(emp, *, info: bool = False) -> tuple[str, list[dict]]:
    """Текст и кнопки текущего шага знакомства. Всё знакомство — одно сообщение."""
    st = _state(emp)
    if st == S_FORM:
        return (T.WELCOME.format(name=emp.first_name) + "\n\n" + T.ASK_FORM,
                keyboard([callback(T.FORM_TY, "ob:ty"), callback(T.FORM_VY, "ob:vy")]))
    if st == S_CONSENT:
        text = T.tv(emp, T.CONSENT)
        if info:
            return (text + "\n\n" + T.tv(emp, T.CONSENT_INFO),
                    keyboard([callback(T.CONSENT_YES, "ob:consent")]))
        return text, keyboard([callback(T.CONSENT_YES, "ob:consent"), callback(T.CONSENT_MORE, "ob:info")])
    if st == S_AI:
        return T.tv(emp, T.ASK_AI), keyboard([callback(T.AI_YES, "ob:ai_yes"), callback(T.AI_NO, "ob:ai_no")])
    return onboarding_done_text(emp), []


def onboarding_done_text(emp) -> str:
    return "\n\n".join([T.tv(emp, T.NICE_TO_MEET).format(name=emp.first_name),
                        T.tv(emp, T.INTRO), T.tv(emp, T.ASK_ANYTIME)])


async def start_onboarding(ctx: BotCtx, repo, emp) -> None:
    """Новое сообщение знакомства (первый раз или /start посреди знакомства)."""
    if _state(emp) not in ONBOARDING:
        _set_state(emp, S_FORM)
    text, kb = onboarding_view(emp)
    mids = await _say(ctx, repo, emp, text, kb)
    if mids:
        _set_state(emp, _state(emp), onboard_mid=mids[-1])


async def _onboarding_callback(cb: Callback, emp) -> None:
    ctx, repo, p, st = cb.ctx, cb.repo, cb.payload, _state(emp)
    if p in ("ob:ty", "ob:vy") and st == S_FORM:
        emp.address_form = "ty" if p == "ob:ty" else "vy"
        _set_state(emp, S_CONSENT)
        await cb.replace(*onboarding_view(emp), employee=emp)
    elif p == "ob:info" and st == S_CONSENT:
        await cb.replace(*onboarding_view(emp, info=True), employee=emp)
    elif p == "ob:consent" and st == S_CONSENT:
        await repo.add_consent(emp.id, "adaptation", True, T.CONSENT_TEXT_VERSION)
        _set_state(emp, S_AI)
        await cb.replace(*onboarding_view(emp), employee=emp)
    elif p in ("ob:ai_yes", "ob:ai_no") and st == S_AI:
        await repo.add_consent(emp.id, "ai_analysis", p == "ob:ai_yes", T.CONSENT_TEXT_VERSION)
        emp.status = "active"
        _set_state(emp, None, onboarded=True)
        await cb.replace(onboarding_done_text(emp), employee=emp)
        after = hooks.single("onboarded")
        if after is not None:
            await after(ctx, repo, emp)
    elif st in ONBOARDING:
        # кнопка из старого сообщения: показываем актуальный шаг в нём же
        await cb.replace(*onboarding_view(emp), employee=emp)
    else:
        await cb.mark()


# --- привязка ---

async def bind_by_code(ctx: BotCtx, repo, code: str, user_id: int, chat_id: int | None,
                       user: dict | None = None) -> bool:
    """Ищем код у новичков, затем у персонала. True — код найден."""
    emp = await repo.employee_by_invite(code)
    if emp is not None:
        if emp.max_user_id and emp.max_user_id != user_id:
            await ctx.sender.send(repo, T.CODE_TAKEN, chat_id=chat_id, user_id=user_id)
            return True
        emp.max_user_id = user_id
        emp.chat_id = chat_id
        _sync_test_name(emp, user)
        if not _onboarded(emp):
            await start_onboarding(ctx, repo, emp)
        else:
            if emp.status == "stopped":
                emp.status = "active"
            await _say(ctx, repo, emp, T.tv(emp, T.WELCOME_BACK))
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


# --- обращения к HR ---

async def create_hr_ticket(ctx: BotCtx, repo, emp, text: str, source: str, **kw):
    """Создать обращение и уведомить HR. Сообщение новичку пишет вызывающий код."""
    factory = hooks.single("create_ticket")
    if factory is not None:
        return await factory(ctx, repo, emp, text, source=source, **kw)
    ticket = await repo.create_ticket(employee_id=emp.id, source=source, text=text,
                                      assignee_id=getattr(emp, "recruiter_id", None),
                                      category=kw.get("category"), is_personal=kw.get("is_personal", False))
    await _notify_hr_group(ctx, repo, T.HR_GROUP_TICKET.format(
        id=ticket.id, name=emp.full_name, site=emp.site_code, text=text))
    return ticket


async def _hr_callback(cb: Callback, emp) -> None:
    if cb.payload == "hr:forward":
        question = (emp.dialog_data or {}).get("last_question")
        if question:
            _set_state(emp, None, last_question=None)
            await create_hr_ticket(cb.ctx, cb.repo, emp, question, source="unknown")
            # «Передать вопрос HR?» превращается в «Передал HR» в том же сообщении
            await cb.replace(T.tv(emp, T.HR_DONE), employee=emp)
        else:
            _set_state(emp, S_HR_QUESTION)
            await cb.replace(T.tv(emp, T.HR_ASK), employee=emp)
    else:
        await cb.mark()


# --- маршрут свободного текста ---

async def route_free_text(ctx: BotCtx, repo, emp, text: str) -> None:
    """Вопрос новичка → база знаний (через hooks). Пока модуля нет — предложить передать HR."""
    handler = hooks.single("free_text")
    if handler is not None:
        await handler(ctx, repo, emp, text)
        return
    _set_state(emp, emp.dialog_state, last_question=text)
    await _say(ctx, repo, emp, T.tv(emp, T.FREE_TEXT_STUB), keyboard([callback(T.FORWARD_HR, "hr:forward")]))


# --- события ---

async def on_bot_started(ctx: BotCtx, repo, upd: dict) -> None:
    user = upd.get("user") or {}
    chat_id = upd.get("chat_id")
    payload = (upd.get("payload") or "").strip()
    await handle_start(ctx, repo, user.get("user_id"), chat_id, payload, user)


async def handle_start(ctx: BotCtx, repo, user_id: int, chat_id: int | None, code: str,
                       user: dict | None = None) -> None:
    if code and CODE_RE.match(code):
        if await bind_by_code(ctx, repo, code, user_id, chat_id, user):
            return
        # неизвестный код, но пользователь уже привязан — просто продолжаем
        if not await _resume_bound(ctx, repo, user_id, chat_id, user):
            await ctx.sender.send(repo, T.CODE_NOT_FOUND, chat_id=chat_id, user_id=user_id)
        return
    if not await _resume_bound(ctx, repo, user_id, chat_id, user):
        await ctx.sender.send(repo, T.ASK_CODE, chat_id=chat_id, user_id=user_id)


async def _resume_bound(ctx: BotCtx, repo, user_id: int, chat_id: int | None, user: dict | None = None) -> bool:
    """/start от уже привязанного пользователя."""
    emp = await repo.employee_by_user(user_id)
    if emp is not None:
        if chat_id:
            emp.chat_id = chat_id
        _sync_test_name(emp, user)
        if not _onboarded(emp):
            await start_onboarding(ctx, repo, emp)
        elif emp.status == "stopped":
            emp.status = "active"
            await _say(ctx, repo, emp, T.tv(emp, T.WELCOME_BACK))
        else:
            await _say(ctx, repo, emp, T.tv(emp, T.HELLO_AGAIN))
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
    cmd = cmd.lower().split("@")[0] if cmd.startswith("/") else ""

    if cmd == "/start":
        await handle_start(ctx, repo, user_id, chat_id, arg.strip(), sender)
        return

    if emp is None and staff is None:
        # запасной вход: код текстом
        if text and CODE_RE.match(text):
            if await bind_by_code(ctx, repo, text, user_id, chat_id, sender):
                return
            await ctx.sender.send(repo, T.CODE_NOT_FOUND, chat_id=chat_id, user_id=user_id)
            return
        await ctx.sender.send(repo, T.ASK_CODE, chat_id=chat_id, user_id=user_id)
        return

    if staff is not None:
        if chat_id and not staff.chat_id:
            staff.chat_id = chat_id
        handler = hooks.single("staff_text")
        if handler is not None:
            await handler(ctx, repo, staff, text, body)
        else:
            await greet_staff(ctx, repo, staff)
        return

    if chat_id and not emp.chat_id:
        emp.chat_id = chat_id

    if _state(emp) in ONBOARDING:
        await _say(ctx, repo, emp, T.tv(emp, T.PRESS_BUTTON))
        return

    # команды
    if cmd == "/hr":
        _set_state(emp, S_HR_QUESTION)
        await _say(ctx, repo, emp, T.tv(emp, T.HR_ASK))
        return
    if cmd:
        handler = hooks.command_handler(cmd.lstrip("/"))
        if handler is not None:
            await handler(ctx, repo, emp, arg.strip())
            return
        if cmd == "/food":
            await _say(ctx, repo, emp, T.tv(emp, T.FOOD_STUB))
            return

    # ждём текст по состоянию диалога
    if _state(emp) == S_HR_QUESTION:
        if not text:
            await _say(ctx, repo, emp, T.tv(emp, T.HR_ASK))
            return
        _set_state(emp, None)
        ticket = await create_hr_ticket(ctx, repo, emp, text, source="hr_command")
        await _say(ctx, repo, emp, T.tv(emp, T.HR_DONE), ticket_id=getattr(ticket, "id", None))
        return
    handler = hooks.state_handler(_state(emp))
    if handler is not None:
        await handler(ctx, repo, emp, text, body)
        return

    # ответ в открытое обращение и прочие перехваты до базы знаний
    for pre in hooks.pre_routes():
        if await pre(ctx, repo, emp, text, body):
            return

    if not text:
        return
    await route_free_text(ctx, repo, emp, text)


def _button_label(body: dict, payload: str) -> str | None:
    """Подпись нажатой кнопки — из клавиатуры исходного сообщения."""
    for att in body.get("attachments") or []:
        if att.get("type") != "inline_keyboard":
            continue
        for row in (att.get("payload") or {}).get("buttons") or []:
            for btn in row:
                if btn.get("payload") == payload:
                    return btn.get("text")
    return None


async def on_message_callback(ctx: BotCtx, repo, upd: dict) -> None:
    cbd = upd.get("callback") or {}
    msg = upd.get("message") or {}
    body = msg.get("body") or {}
    payload = cbd.get("payload") or ""
    cb = Callback(ctx=ctx, repo=repo, callback_id=cbd.get("callback_id"), payload=payload,
                  label=_button_label(body, payload), orig_text=body.get("text") or "", mid=body.get("mid"),
                  chat_id=(msg.get("recipient") or {}).get("chat_id"),
                  user_id=(cbd.get("user") or {}).get("user_id"))
    try:
        emp = await repo.employee_by_user(cb.user_id)
        staff = None if emp else await repo.staff_by_user(cb.user_id)
        if emp is None and staff is None:
            await cb.toast(T.NOT_BOUND)
            return
        cb.is_staff = staff is not None
        if emp is not None and cb.chat_id and not emp.chat_id:
            emp.chat_id = cb.chat_id
        await repo.add_message(employee_id=getattr(emp, "id", None), staff_id=getattr(staff, "id", None),
                               chat_id=cb.chat_id, direction="in", text=f"[кнопка] {cb.label or payload}", mid=None)

        prefix = payload.split(":", 1)[0]
        if prefix == "ob" and emp is not None:
            await _onboarding_callback(cb, emp)
        elif prefix == "hr" and emp is not None:
            await _hr_callback(cb, emp)
        else:
            handler = hooks.callback_handler(prefix)
            if handler is not None:
                await handler(cb, emp or staff)
    finally:
        # нажатие нужно закрыть всегда, иначе у пользователя крутится загрузка
        if not cb.answered:
            if cb.label:
                await cb.mark()
            else:
                await cb.toast("Принято")


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
