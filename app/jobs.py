"""Реестр фоновых задач. Модули регистрируют задачи, main.py запускает их в APScheduler.

    from app.jobs import job

    @job("scenario_tick", seconds=60)
    async def tick() -> None: ...           # сама открывает сессию БД

    @job("weekly_report", cron={"day_of_week": "mon", "hour": 9})
    async def weekly() -> None: ...

Время cron — Europe/Moscow. Задача не должна падать: ошибки логируются планировщиком.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class Job:
    name: str
    func: Callable[[], Awaitable[None]]
    seconds: int | None = None
    cron: dict | None = None


_JOBS: dict[str, Job] = {}


def job(name: str, *, seconds: int | None = None, cron: dict | None = None):
    if (seconds is None) == (cron is None):
        raise ValueError("Укажите seconds или cron")

    def deco(fn):
        _JOBS[name] = Job(name, fn, seconds, cron)
        return fn
    return deco


def registered() -> list[Job]:
    return list(_JOBS.values())
