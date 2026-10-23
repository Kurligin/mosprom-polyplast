"""Подключение опросов к боту: кнопки sv:, текст в состоянии sv:, напоминания о непройденном опросе."""

from __future__ import annotations

from app.bot import hooks
from app.jobs import job
from app.surveys import engine


@hooks.on_callback(engine.PREFIX)
async def survey_button(cb: hooks.Callback, person) -> None:
    await engine.handle_callback(cb, person)


@hooks.on_state(engine.PREFIX)
async def survey_text(ctx, repo, emp, text: str, msg: dict) -> bool | None:
    # False — опрос этот текст уже не ждёт, бот ведёт его обычным маршрутом
    return await engine.handle_text(ctx, repo, emp, text, msg)


@job("survey_reminders", seconds=60)
async def survey_reminders() -> None:
    """На следующий и через рабочий день после начала опроса, не раньше 10:00 по времени площадки."""
    from app.db.session import async_session

    async with async_session() as s:
        await engine.remind_all(s)
        await s.commit()
