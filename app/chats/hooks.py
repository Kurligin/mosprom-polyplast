"""Подключение чатов команды к боту и планировщику: кнопки ch:, задача team_chats, чаты сразу после знакомства.

События bot_added / bot_removed вызывает app/bot/handlers.py (app.chats.service.register / unregister).
"""

from __future__ import annotations

from app.bot import hooks
from app.chats import service
from app.jobs import job


@job("team_chats", seconds=300)
async def team_chats() -> None:
    await service.tick()


@hooks.on_callback("ch")
async def chats_button(cb: hooks.Callback, person) -> None:
    await service.on_button(cb, person)


# Кнопку «Сделать эту группу HR-группой» видят все в группе: не подключённым к боту — всплывашка от модуля
chats_button.anyone = True


# Обработчик «после знакомства» один (у сценария). Чаты встают после него: сначала шаги дня, потом чаты.
_prev = hooks.single("onboarded")
if getattr(_prev, "chats_chain", False):  # модуль перезагрузили без hooks.reset(): не оборачиваем себя
    _prev = _prev.prev


@hooks.on_onboarded
async def after_onboarding(ctx, repo, emp) -> None:
    if _prev is not None:
        await _prev(ctx, repo, emp)
    await service.on_onboarded(ctx, repo, emp)


after_onboarding.chats_chain = True
after_onboarding.prev = _prev
