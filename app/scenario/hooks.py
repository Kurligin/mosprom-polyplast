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
async def scenario_state(ctx, repo, emp, text: str, msg: dict) -> bool:
    """Старое состояние sc:intro (до 23.10) переводим в ожидание в dialog_data, остальное — сбрасываем.

    False — текст идёт обычным маршрутом (красный флаг, окно ответа HR, рассказ о себе, база знаний).
    """
    if emp.dialog_state == engine.INTRO_STATE and (emp.dialog_data or {}).get(engine.INTRO_WAIT) is None:
        engine._set_data(emp, **{engine.INTRO_WAIT: {"since": engine._vnow(emp).isoformat(), "asked": []}})
    emp.dialog_state = None
    return False


@hooks.pre_route
async def intro_for_colleagues(ctx, repo, emp, text: str, msg: dict) -> bool:
    """Рассказ о себе и фото после просьбы «представлю тебя коллегам». Вопросы и реплики идут дальше."""
    return await engine.on_intro(ctx, repo, emp, text, msg)


@hooks.on_command("food")
async def food(ctx, repo, emp, arg: str) -> None:
    await engine.send_food(ctx, repo, emp, arg)


@hooks.on_onboarded
async def after_onboarding(ctx, repo, emp) -> None:
    await engine.on_onboarded(ctx, repo, emp)
