"""Вход в Mini App по initData «МАКС».

Алгоритм (dev.max.ru/docs/webapps/validation), как в Telegram:
1. initData — строка «key=value&…» (WebAppData). Параметр hash — ровно один, каждый ключ — один раз.
2. Значения URL-декодируются, пары без hash сортируются по ключу и склеиваются через «\\n».
3. secret_key = HMAC-SHA256(key="WebAppData", msg=<токен бота>).
4. hash = hex(HMAC-SHA256(key=secret_key, msg=<строка из п. 2>)).
auth_date — не старше 24 часов. Роль — по user.id → staff.max_user_id.
Если в заголовке нет «=», Mini App закодировал строку целиком (encodeURIComponent из-за кириллицы):
раскодируем её один раз и проверяем как обычно.

Без initData при DEV_ALLOW_NO_INIT_DATA=true запрос проходит как первый HR из БД
(заголовок X-Dev-Role: manager | head | hr выбирает другую роль — только в dev).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Annotated
from urllib.parse import parse_qsl, unquote, unquote_plus

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep
from app.config import settings
from app.db.models import Staff

HEADER = "X-Max-Init-Data"
DEV_ROLE_HEADER = "X-Dev-Role"
MAX_AGE = 24 * 3600
FUTURE_SKEW = 300  # часы клиента и сервера могут немного расходиться

ROLE_MAP = {"hr": "hr", "recruiter": "hr", "manager": "manager", "head_recruitment": "head"}
DEV_ROLES = {"hr": ("hr", "recruiter"), "manager": ("manager",), "head": ("head_recruitment",)}


class InitDataError(ValueError):
    """initData не прошла проверку. Текст — для человека."""


def _pairs(raw: str) -> list[tuple[str, str]]:
    raw = raw.strip().lstrip("#")
    if raw and "=" not in raw:
        # Mini App закодировал строку encodeURIComponent: в заголовке HTTP нельзя кириллицу (имя в user)
        raw = unquote(raw).strip().lstrip("#")
    if "WebAppData=" in raw:
        # пришёл целиком фрагмент URL: #WebAppData=…&WebAppPlatform=…
        outer = parse_qsl(raw, keep_blank_values=True)
        values = [v for k, v in outer if k == "WebAppData"]
        if len(values) != 1:
            raise InitDataError("нет данных запуска")
        raw = values[0]
    pairs = []
    for part in raw.split("&"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise InitDataError("неверный формат данных запуска")
        pairs.append((key, value))
    return pairs


def sign(data: dict[str, str], token: str) -> str:
    """Подпись набора параметров (уже декодированных). Нужна тестам и проверке."""
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def validate_init_data(raw: str, token: str, *, max_age: int = MAX_AGE, now: float | None = None) -> dict:
    """Проверить подпись и срок. Возвращает параметры; user — словарь."""
    if not token:
        raise InitDataError("на сервере не задан токен бота")
    pairs = _pairs(raw)
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise InitDataError("параметр повторяется")
    received = [v for k, v in pairs if k == "hash"]
    if len(received) != 1 or not received[0]:
        raise InitDataError("нет подписи")
    got = received[0].lower()
    rest = [(k, v) for k, v in pairs if k != "hash"]

    data = None
    # Платформа может отдать значения закодированными один или два раза — проверяем варианты
    for decode in (unquote, unquote_plus, lambda v: v, lambda v: unquote(unquote(v))):
        candidate = {k: decode(v) for k, v in rest}
        if hmac.compare_digest(sign(candidate, token), got):
            data = candidate
            break
    if data is None:
        raise InitDataError("подпись не сходится")

    try:
        auth_date = int(data.get("auth_date", ""))
    except ValueError:
        raise InitDataError("нет даты входа") from None
    now = time.time() if now is None else now
    if now - auth_date > max_age:
        raise InitDataError("данные входа устарели, откройте панель заново")
    if auth_date - now > FUTURE_SKEW:
        raise InitDataError("неверная дата входа")

    try:
        user = json.loads(data.get("user") or "null")
    except json.JSONDecodeError:
        raise InitDataError("неверные данные пользователя") from None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InitDataError("нет пользователя")
    return {**data, "user": user, "auth_date": auth_date}


def role_of(staff: Staff) -> str | None:
    """Роль в Mini App: hr | manager | head."""
    return ROLE_MAP.get(staff.role)


async def _dev_staff(session: AsyncSession, role: str | None) -> Staff | None:
    roles = DEV_ROLES.get((role or "hr").lower(), DEV_ROLES["hr"])
    for r in roles:
        staff = await session.scalar(select(Staff).where(Staff.role == r).order_by(Staff.id).limit(1))
        if staff is not None:
            return staff
    return None


async def current_staff(request: Request, session: SessionDep) -> Staff:
    raw = (request.headers.get(HEADER) or "").strip()
    if raw:
        try:
            data = validate_init_data(raw, settings.max_bot_token)
        except InitDataError as e:
            raise HTTPException(401, f"Вход не подтверждён: {e}") from None
        staff = await session.scalar(select(Staff).where(Staff.max_user_id == data["user"]["id"]))
        if staff is None or role_of(staff) is None:
            raise HTTPException(403, "Панель для HR и руководителей. Подключитесь к боту по ссылке от HR.")
        return staff
    if settings.dev_allow_no_init_data:
        staff = await _dev_staff(session, request.headers.get(DEV_ROLE_HEADER))
        if staff is None:
            raise HTTPException(401, "В базе нет сотрудников HR. Запустите make seed.")
        return staff
    raise HTTPException(401, "Откройте панель из «МАКС»")


StaffDep = Annotated[Staff, Depends(current_staff)]


async def hr_staff(staff: StaffDep) -> Staff:
    """HR, рекрутер или глава подбора. Руководителю — 403."""
    if role_of(staff) not in ("hr", "head"):
        raise HTTPException(403, "Раздел доступен только HR")
    return staff


HrDep = Annotated[Staff, Depends(hr_staff)]
