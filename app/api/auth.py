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
Обход работает только для запроса с этой машины (dev_bypass_allowed): не через прокси или туннель,
по адресу localhost или имени сервиса Docker, не с чужого сайта. Порты стенда открыты только на 127.0.0.1.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import time
from typing import Annotated
from urllib.parse import parse_qsl, unquote, unquote_plus, urlsplit

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


# Заголовки, которые добавляют прокси и туннели (Caddy, nginx, Cloudflare, ngrok …): запрос пришёл снаружи
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port",
                 "x-real-ip", "x-client-ip", "true-client-ip", "cf-connecting-ip", "cf-ray", "cf-visitor",
                 "x-original-forwarded-for", "via")


def _local_host(host: str | None) -> bool:
    """localhost, 127.0.0.1, ::1 или имя без точек (сервис Docker: app, miniapp). Адрес в сети и домен — нет."""
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return host == "localhost" or host.endswith(".localhost") or "." not in host


def _local_url(value: str) -> bool:
    try:
        return _local_host(urlsplit(value).netloc.rsplit("@", 1)[-1])
    except ValueError:
        return False


def _local_peer(peer: str | None) -> bool:
    """Прямой собеседник: loopback или внутренняя сеть Docker. Внешний адрес — нет."""
    if not peer:
        return True
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return True  # тестовый клиент без адреса
    return ip.is_loopback or ip.is_private


def dev_bypass_allowed(request: Request) -> bool:
    """Можно ли пустить запрос без initData (DEV_ALLOW_NO_INIT_DATA): только браузер или curl с этой машины.

    Нельзя, если запрос прошёл через прокси или туннель (их заголовки), пришёл по внешнему имени или адресу
    в локальной сети, с чужого сайта (Origin, Referer, Sec-Fetch-Site) или с внешнего адреса.
    """
    if not settings.dev_allow_no_init_data:
        return False
    h = request.headers
    if any(name in h for name in PROXY_HEADERS):
        return False
    if "host" in h and not _local_host(h["host"]):
        return False
    origin = h.get("origin")
    if origin is not None and not _local_url(origin):
        return False  # в том числе «Origin: null» (песочница, file://)
    referer = h.get("referer")
    if referer and not _local_url(referer):
        return False
    if (h.get("sec-fetch-site") or "").lower() == "cross-site":
        return False
    return _local_peer(request.client.host if request.client else None)


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
    if dev_bypass_allowed(request):
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
