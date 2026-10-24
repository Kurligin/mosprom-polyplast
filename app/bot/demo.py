"""Демо-режим в чате: /demo меняет роль без кодов из приглашений (только DEMO_ENABLED=true).

На защите судьи и команда переключаются между тестовыми новичками и демо-персоналом прямо в боте.
Роли — только из демо-данных: тестовые новички (is_test) и персонал из сида (seed.seed.STAFF).
Аккаунт настоящего сотрудника переключение не трогает, настоящих людей к чужому аккаунту не привязывает.

Кнопки «dm:…»: dm:menu, dm:emp:<площадка>, dm:staff:<роль>, dm:shift, dm:to:<точка>, dm:reset.
Ответ на нажатие меняет то же сообщение.
"""

from __future__ import annotations

import logging

from app.bot import texts as T
from app.bot.handlers import (
    ONBOARDING,
    S_FORM,
    BotCtx,
    _is_demo,
    _onboarded,
    _set_state,
    _state,
    onboarding_view,
    staff_greeting,
)
from app.bot.hooks import Callback
from app.bot.keyboards import callback, keyboard
from app.config import settings

log = logging.getLogger(__name__)


def demo_staff_names() -> dict[str, str]:
    """Роль → ФИО демо-сотрудника, как его заводит сид. Другой персонал демо-меню не трогает."""
    from seed.seed import STAFF

    return {role: name for name, role, _site in STAFF}


def is_demo_staff(staff) -> bool:
    return demo_staff_names().get(getattr(staff, "role", None)) == getattr(staff, "full_name", None)


def _role_label(emp, staff) -> str | None:
    if emp is not None and getattr(emp, "is_test", False):
        return T.DEMO_ROLES.get(f"emp:{emp.site_code}")
    if staff is not None:
        return T.DEMO_ROLES.get(f"staff:{staff.role}")
    return None


async def _menu(repo, user_id: int | None) -> tuple[str, list[dict]]:
    emp = await repo.employee_by_user(user_id) if user_id else None
    staff = await repo.staff_by_user(user_id) if user_id else None
    text = T.DEMO_MENU
    label = _role_label(emp, staff)
    if label:
        text = T.DEMO_NOW.format(role=label) + "\n\n" + text
    keys = list(T.DEMO_ROLES)
    rows = [[callback(T.DEMO_ROLES[k], f"dm:{k}") for k in keys[i:i + 2]] for i in range(0, len(keys), 2)]
    if emp is not None and emp.is_test:
        rows.append([callback(T.DEMO_SHIFT, "dm:shift")])
        rows.append([callback(T.DEMO_RESET, "dm:reset")])
    return text, keyboard(*rows)


async def show_menu(ctx: BotCtx, repo, user_id: int | None, chat_id: int | None) -> None:
    """Новое сообщение с демо-меню (команда /demo или /start без кода в демо)."""
    text, kb = await _menu(repo, user_id)
    await ctx.sender.send(repo, text, chat_id=chat_id, user_id=user_id, attachments=kb)


def _shift_keyboard() -> list[dict]:
    pts = list(T.DEMO_SHIFT_LABELS)
    return keyboard([callback(T.DEMO_SHIFT_LABELS[p], f"dm:to:{p}") for p in pts[:3]],
                    [callback(T.DEMO_SHIFT_LABELS[p], f"dm:to:{p}") for p in pts[3:]],
                    [callback(T.DEMO_BACK, "dm:menu")])


def _back_keyboard() -> list[dict]:
    return keyboard([callback(T.DEMO_BACK, "dm:menu")])


async def _switch(cb: Callback, kind: str, key: str) -> None:
    """Отвязать аккаунт от прежней демо-личности и привязать к выбранной."""
    repo, uid = cb.repo, cb.user_id
    label = T.DEMO_ROLES.get(f"{kind}:{key}", key)
    cur_emp = await repo.employee_by_user(uid)
    cur_staff = await repo.staff_by_user(uid)
    if (cur_emp is not None and not _is_demo(cur_emp)) or (cur_staff is not None and not is_demo_staff(cur_staff)):
        await cb.replace(T.DEMO_REAL)
        return

    if kind == "emp":
        target = await repo.demo_employee(key)
        if target is not None and not target.is_test:
            target = None
    else:
        name = demo_staff_names().get(key)
        target = await repo.demo_staff(key, name) if name else None
    if target is None:
        await cb.replace(T.DEMO_NO_PERSON.format(role=label), _back_keyboard())
        return

    # прежние личности этого аккаунта (кроме выбранной) и прежний хозяин выбранной — отвязываем
    for prev in (cur_emp, cur_staff):
        if prev is not None and prev is not target:
            prev.max_user_id = None
            prev.chat_id = None
    if target.max_user_id and target.max_user_id != uid:
        log.info("Демо: роль «%s» забрана у аккаунта %s", label, target.max_user_id)
        target.max_user_id = None
        target.chat_id = None
    await repo.flush()  # max_user_id уникален: сначала отвязка, потом привязка
    target.max_user_id = uid
    target.chat_id = cb.chat_id
    await repo.flush()

    if kind == "staff":
        await cb.replace(*staff_greeting(cb.ctx, target), staff=target)
        return
    await _after_newcomer(cb, target)


async def _after_newcomer(cb: Callback, emp) -> None:
    """Тот же поток, что после привязки по коду, но в том же сообщении: знакомство или «С возвращением»."""
    if not _onboarded(emp):
        if _state(emp) not in ONBOARDING:
            _set_state(emp, S_FORM)
        _set_state(emp, _state(emp), onboard_mid=cb.mid)
        await cb.replace(*onboarding_view(emp), employee=emp)
        return
    if emp.status == "stopped":
        emp.status = "active"
    await cb.replace(T.tv(emp, T.WELCOME_BACK), employee=emp)


async def _test_newcomer(cb: Callback):
    emp = await cb.repo.employee_by_user(cb.user_id)
    if emp is None or not emp.is_test:
        await cb.replace(T.DEMO_ONLY_TEST, _back_keyboard())
        return None
    return emp


async def on_callback(cb: Callback) -> None:
    if not settings.demo_enabled:
        await cb.toast(T.DEMO_OFF)
        return
    if not cb.user_id:
        # без аккаунта employee_by_user(None) искал бы всех непривязанных
        await cb.toast(T.DEMO_STALE)
        return
    parts = cb.payload.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "menu":
        await cb.replace(*await _menu(cb.repo, cb.user_id))
    elif action in ("emp", "staff") and len(parts) == 3 and f"{action}:{parts[2]}" in T.DEMO_ROLES:
        await _switch(cb, action, parts[2])
    elif action == "shift":
        if await _test_newcomer(cb) is not None:
            await cb.replace(T.DEMO_SHIFT_ASK, _shift_keyboard())
    elif action == "to" and len(parts) == 3 and parts[2] in T.DEMO_SHIFT_LABELS:
        emp = await _test_newcomer(cb)
        if emp is None:
            return
        result = await cb.repo.time_travel(emp, parts[2])
        await cb.replace(str(result.get("message") or T.DEMO_SHIFT_ASK), _back_keyboard(), employee=emp)
    elif action == "reset":
        emp = await _test_newcomer(cb)
        if emp is None:
            return
        await cb.repo.reset_progress(emp)
        await _after_newcomer(cb, emp)  # знакомство заново в этом же сообщении
    else:
        await cb.toast(T.DEMO_STALE)
