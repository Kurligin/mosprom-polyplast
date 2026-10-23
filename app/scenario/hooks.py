"""Подключение сценария к боту и планировщику: тик, кнопки sc:, /food, после знакомства, «представь себя»."""

from __future__ import annotations

from app.bot import hooks
from app.jobs import job
from app.scenario import engine


@job("scenario_tick", seconds=60)
async def scenario_tick() -> None:
    await engine.tick()


@hooks.on_callback("sc")
async def scenario_button(cb: hooks.Callback, person) -> None:
    await engine.on_button(cb, person)


@hooks.on_state("sc")
async def scenario_state(ctx, repo, emp, text: str, msg: dict) -> None:
    if emp.dialog_state == engine.INTRO_STATE:
        await engine.on_intro(ctx, repo, emp, text, msg)
        return
    # неизвестное состояние сценария: не держим новичка в нём, текст — как обычный вопрос
    emp.dialog_state = None
    if text:
        from app.bot.handlers import route_free_text

        await route_free_text(ctx, repo, emp, text)


@hooks.on_command("food")
async def food(ctx, repo, emp, arg: str) -> None:
    await engine.send_food(ctx, repo, emp, arg)


@hooks.on_onboarded
async def after_onboarding(ctx, repo, emp) -> None:
    await engine.on_onboarded(ctx, repo, emp)
