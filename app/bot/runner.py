"""Запуск бота: снять webhook, выставить меню команд, бесконечный long polling."""

from __future__ import annotations

import asyncio
import logging

from app.bot import runtime
from app.bot.api import UPDATE_TYPES, MaxApi
from app.bot.handlers import BotCtx, handle_update
from app.bot.sender import Sender
from app.bot.texts import COMMANDS
from app.config import settings

log = logging.getLogger("korsik.bot")

POLL_TIMEOUT = 30      # секунд long polling
ERROR_PAUSE = 3        # пауза после сетевой ошибки


async def _startup(api: MaxApi) -> None:
    """Снять webhook-подписки (long polling с ними не работает) и выставить меню."""
    try:
        for sub in await api.get_subscriptions():
            url = sub.get("url")
            if url:
                await api.delete_subscription(url)
                log.info("Снята webhook-подписка")
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось проверить подписки: %s", e)
    try:
        await api.set_commands(COMMANDS)
        log.info("Меню команд обновлено")
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось выставить меню команд: %s", e)
    try:
        me = await api.get_me()
        log.info("Бот: %s (@%s)", me.get("name") or me.get("first_name"), me.get("username"))
    except Exception as e:  # noqa: BLE001
        log.warning("GET /me: %s", e)


async def process_update(ctx: BotCtx, upd: dict) -> None:
    """Одно событие — одна транзакция."""
    from app.bot.repo import SqlRepo
    from app.db.session import async_session

    async with async_session() as session:
        repo = SqlRepo(session)
        try:
            await handle_update(ctx, repo, upd)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _error_pause(e: Exception) -> float:
    """Пауза перед повтором /updates: на 429 — сколько просит «МАКС» (Retry-After), иначе ERROR_PAUSE."""
    retry_after = getattr(e, "retry_after", None)
    if getattr(e, "is_rate_limited", False) and retry_after:
        return min(float(retry_after), 60.0)
    return ERROR_PAUSE


MARKER_KEY = "max_updates_marker"


async def _load_marker() -> int | None:
    """Маркер long polling переживает перезапуск (uvicorn --reload), иначе события придут повторно."""
    from app.bot.repo import SqlRepo
    from app.db.session import async_session

    try:
        async with async_session() as session:
            value = await SqlRepo(session).get_setting(MARKER_KEY)
            return int(value) if value is not None else None
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось прочитать маркер: %s", e)
        return None


async def _save_marker(marker: int) -> None:
    from app.bot.repo import SqlRepo
    from app.db.session import async_session

    try:
        async with async_session() as session:
            await SqlRepo(session).set_setting(MARKER_KEY, marker)
            await session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось сохранить маркер: %s", e)


async def run_bot() -> None:
    if not settings.max_bot_token:
        log.warning("MAX_BOT_TOKEN не задан — бот не запущен")
        return
    api = MaxApi(settings.max_bot_token, settings.max_api_base_url, settings.ca_bundle_file)
    sender = Sender(api)
    ctx = BotCtx(sender=sender, bot_name=settings.max_bot_name, miniapp_url=settings.miniapp_url)
    runtime.set_ctx(ctx)  # планировщик и API отправляют сообщения через тот же контекст
    try:
        await _startup(api)
        marker = await _load_marker()
        log.info("Long polling запущен")
        while True:
            try:
                data = await api.get_updates(marker=marker, timeout=POLL_TIMEOUT, types=UPDATE_TYPES)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — сеть/5xx: пауза и повтор
                log.warning("GET /updates: %s", e)
                await asyncio.sleep(_error_pause(e))
                continue
            updates = data.get("updates") or []
            for upd in updates:
                try:
                    await process_update(ctx, upd)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Ошибка обработки события %s", upd.get("update_type"))
            new_marker = data.get("marker")
            if new_marker is not None and new_marker != marker:
                marker = new_marker
                # сохраняем после обработки: при перезапуске посреди пачки события придут повторно
                await _save_marker(marker)
    except asyncio.CancelledError:
        log.info("Бот остановлен")
        raise
    finally:
        runtime.set_ctx(None)
        await api.close()
