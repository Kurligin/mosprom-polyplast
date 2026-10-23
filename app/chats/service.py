"""Чаты команды: учёт групповых чатов «МАКС», HR-группа, добавление новичка, «представлю тебя коллегам».

Учёт (`team_chats`):
- бота добавили в группу (`bot_added`) → чат в учёт: название, ссылка и число участников из GET /chats/{id}.
  Кому нужен — `audience`: none (по умолчанию, пока HR не настроил) | all | sites + `sites`, `work_type`
  сужает до офиса или производства, `for_intro` — чат для представлений. `bot_removed` → `active = False`;
- HR-группа — по-прежнему `Setting('hr_group_chat_id')` (его читают `runtime.notify_hr_group` и модули).
  Пока она не выбрана, бот пишет в новой группе короткое сообщение с кнопкой «Сделать эту группу HR-группой».
  Нажать может только привязанный сотрудник HR, остальным — всплывашка. Переназначить — из Mini App.
  HR-группе новичков не добавляем и представления туда не публикуем.

Добавление новичка (`add_to_chats`): в первый рабочий день (`engine.adaptation_day >= 1`, с машиной времени),
сразу после знакомства или задачей `team_chats` раз в 5 минут. Один раз на чат: итог в
`dialog_data["chats"][str(chat_id)] = {"status": added | link | failed, "at", "title", "error"?, "mid"?}`.
Не добавили (приватность и т. п.) — одно сообщение новичку со ссылками на эти чаты. Синтетику не трогаем.

Представление коллегам: рассказ о себе собран (`engine.on_intro`) → «Спасибо» и вопрос «опубликовать в чате
команды?» одним сообщением (`offer_intro`). Кнопка меняет то же сообщение, без согласия не публикуем.
Итог — `dialog_data["intro_post"] = {"status": asked | published | declined | failed, "chat_id", "mid",
"intro_at", "post_mid"?, "at"}`. Основной чат — с отметкой «для представлений», иначе чат площадки новичка.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot import runtime, uploads
from app.bot.api import MaxApiError
from app.bot.keyboards import callback, keyboard, link
from app.bot.repo import SqlRepo, lock_employee
from app.bot.texts import tv
from app.chats import texts as T
from app.db.models import Employee, Setting, Site, TeamChat
from app.scenario import engine

log = logging.getLogger(__name__)

HR_KEY = "hr_group_chat_id"
CHATS_KEY = "chats"          # dialog_data: куда добавили новичка
POST_KEY = "intro_post"      # dialog_data: публикация рассказа о себе в чате команды
AUDIENCES = ("none", "all", "sites")
WORK_TYPES = ("office", "production")
HR_ROLES = ("hr", "recruiter", "head_recruitment")
PRIVACY = "add.participant.privacy"
INFO_TTL = timedelta(hours=1)  # название, ссылку и число участников перечитываем не чаще раза в час
GONE = ("removed", "left", "closed")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _data(emp) -> dict:
    return dict(emp.dialog_data or {})


def _set_data(emp, **kw) -> None:
    # новый dict, чтобы SQLAlchemy заметил изменение JSONB
    emp.dialog_data = {**(emp.dialog_data or {}), **kw}


def title_of(chat: TeamChat | None) -> str:
    return ((chat.title or "").strip() if chat is not None else "") or "Чат без названия"


def display_name(emp) -> str:
    """«Смирнов Иван (синтетика)» + имя «Иван» → «Иван Смирнов»: в чате команды привычнее имя впереди."""
    words = engine.clean_name(emp.full_name).split()
    first = (emp.first_name or "").strip()
    if not words:
        return first
    if not first or first not in words:
        return " ".join(words[:2])
    surname = words[0] if words[0] != first else (words[1] if len(words) > 1 else "")
    return f"{first} {surname}".strip()


# --- учёт чатов -------------------------------------------------------------------------

async def hr_group_id(session) -> int | None:
    row = await session.get(Setting, HR_KEY)
    try:
        return int(row.value) if row is not None and row.value is not None else None
    except (TypeError, ValueError):
        return None


async def active_chats(session) -> list[TeamChat]:
    return list((await session.scalars(select(TeamChat).where(TeamChat.active.is_(True))
                                       .order_by(TeamChat.chat_id))).all())


def _sender():
    return getattr(runtime.get_ctx(), "sender", None)


async def chat_info(sender, chat_id: int) -> dict | None:
    """GET /chats/{id}. None — не получилось (бот выключен, нет прав, сеть): учёт работает и без этого."""
    api = getattr(sender, "api", None)
    if api is None or not hasattr(sender, "call"):
        return None
    try:
        data = await sender.call(chat_id, api.get_chat, chat_id)
    except Exception as e:  # noqa: BLE001
        log.info("GET /chats/%s: %s", chat_id, e)
        return None
    return data if isinstance(data, dict) else None


def apply_info(chat: TeamChat, info: dict | None) -> None:
    """Название, ссылка, число участников и статус из ответа GET /chats/{id}."""
    if not info:
        return
    if info.get("title"):
        chat.title = str(info["title"])[:255]
    if info.get("link"):
        chat.link = str(info["link"])[:500]
    if isinstance(info.get("participants_count"), int):
        chat.members_count = info["participants_count"]
    if info.get("status") in GONE:
        chat.active = False
        chat.prompt_mid = None
    chat.info_at = utcnow()


async def _get_or_create(session, chat_id: int) -> TeamChat:
    chat = await session.get(TeamChat, chat_id)
    if chat is None:
        chat = TeamChat(chat_id=chat_id, audience="none", sites=[], for_intro=False, active=True)
        session.add(chat)
    return chat


async def register(ctx, repo, chat_id: int, user: dict | None = None) -> TeamChat:
    """bot_added: чат в учёт (или снова активен) и одно сообщение в группе.

    HR-группа не выбрана — с кнопкой «Сделать эту группу HR-группой», иначе короткое знакомство.
    """
    s = repo.s
    chat = await _get_or_create(s, chat_id)
    chat.active = True
    chat.added_by = (user or {}).get("user_id") or chat.added_by
    apply_info(chat, await chat_info(ctx.sender, chat_id))
    chat.active = True  # событие свежее, чем статус в GET /chats
    await s.flush()
    hr_id = await hr_group_id(s)
    kb = None
    if hr_id == chat_id:
        text = T.HR_GROUP_DONE
    elif hr_id is None:
        text = T.GROUP_PROMPT
        kb = keyboard([callback(T.MAKE_HR_BUTTON, f"ch:hr:{chat_id}")])
    else:
        text = T.TEAM_HELLO
    try:
        mids = await ctx.sender.send(repo, text, chat_id=chat_id, attachments=kb)
    except Exception as e:  # noqa: BLE001 — чат в учёте и без приветствия
        log.warning("Сообщение в новую группу %s не отправлено: %s", chat_id, e)
        mids = None
    chat.prompt_mid = mids[-1] if mids and kb else None
    log.info("Чат команды %s в учёте", chat_id)
    return chat


async def unregister(repo, chat_id: int) -> None:
    """bot_removed: чат выключается. Была HR-группой — HR-группы больше нет."""
    s = repo.s
    chat = await s.get(TeamChat, chat_id)
    if chat is not None:
        chat.active = False
        chat.prompt_mid = None
    if await hr_group_id(s) == chat_id:
        await SqlRepo(s).set_setting(HR_KEY, None)


async def ensure_hr_row(session) -> None:
    """HR-группа, выбранная до учёта чатов (только Setting), тоже появляется в списке чатов."""
    hr_id = await hr_group_id(session)
    if hr_id is not None and await session.get(TeamChat, hr_id) is None:
        session.add(TeamChat(chat_id=hr_id, title="HR-группа", audience="none", sites=[], active=True))
        await session.flush()


async def set_hr_group(session, chat: TeamChat, *, sender=None, repo=None, pressed_mid: str | None = None) -> None:
    """Сделать чат HR-группой: Setting('hr_group_chat_id') + чат больше не для новичков.

    Кнопки «Сделать эту группу HR-группой» в других чатах меняются на месте на короткое знакомство.
    pressed_mid — сообщение с нажатой кнопкой: его меняет сам обработчик кнопки. Из Mini App (pressed_mid
    нет) в новой HR-группе без такого сообщения бот коротко пишет, что теперь присылает сюда обращения.
    """
    if not chat.active:
        raise ValueError("Бота удалили из этого чата: сначала добавьте его обратно")
    repo = repo or SqlRepo(session)
    prev = await hr_group_id(session)
    await repo.set_setting(HR_KEY, chat.chat_id)
    chat.audience, chat.sites, chat.work_type, chat.for_intro = "none", [], None, False
    told = pressed_mid is not None
    pending = (await session.scalars(select(TeamChat).where(TeamChat.prompt_mid.is_not(None)))).all()
    for c in pending:
        mid, c.prompt_mid = c.prompt_mid, None
        if mid == pressed_mid:
            continue
        text = T.HR_GROUP_DONE if c.chat_id == chat.chat_id else T.TEAM_HELLO
        told = told or c.chat_id == chat.chat_id
        if sender is not None:
            try:
                await sender.edit(mid, text, attachments=[], key=c.chat_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Сообщение с кнопкой в чате %s не изменено: %s", c.chat_id, e)
    if not told and prev != chat.chat_id and sender is not None:
        try:
            await sender.send(repo, T.HR_GROUP_DONE, chat_id=chat.chat_id)
        except Exception as e:  # noqa: BLE001
            log.warning("HR-группа %s не уведомлена: %s", chat.chat_id, e)
    log.info("HR-группа: чат %s", chat.chat_id)


async def update_chat(session, chat: TeamChat, *, fields: dict, sender=None) -> TeamChat:
    """Правила чата из Mini App: audience, sites, work_type, for_intro, is_hr_group (только переданные поля).

    ValueError — текст для HR (неизвестная площадка, правила у HR-группы, неактивный чат).
    """
    hr_id = await hr_group_id(session)
    make_hr = fields.get("is_hr_group")
    is_hr = make_hr is True or (hr_id == chat.chat_id and make_hr is not False)
    if is_hr and (fields.get("audience") not in (None, "none") or fields.get("for_intro")):
        raise ValueError("В HR-группу новичков не добавляем: сначала выберите другую HR-группу")
    if "sites" in fields:
        codes = [str(c).strip() for c in fields["sites"] or [] if str(c).strip()]
        known = set((await session.scalars(select(Site.code).where(Site.code.in_(codes)))).all()) if codes else set()
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise ValueError(f"Нет площадки {', '.join(unknown)}")
        chat.sites = list(dict.fromkeys(codes))
    if "audience" in fields and fields["audience"] is not None:
        if fields["audience"] not in AUDIENCES:
            raise ValueError("Кому нужен чат: none, all или sites")
        chat.audience = fields["audience"]
    if "work_type" in fields:
        wt = fields["work_type"]
        chat.work_type = wt if wt in WORK_TYPES else None
    if fields.get("for_intro") is not None:
        chat.for_intro = bool(fields["for_intro"])
    if make_hr is True and hr_id != chat.chat_id:
        await set_hr_group(session, chat, sender=sender)
    elif make_hr is False and hr_id == chat.chat_id:
        await SqlRepo(session).set_setting(HR_KEY, None)
    await session.flush()
    return chat


# --- кому нужен чат ------------------------------------------------------------------------

def matches(chat: TeamChat, emp) -> bool:
    """Чат нужен новичку по правилам «кому»: всем / площадкам, офису или производству."""
    if not chat.active or chat.audience not in ("all", "sites"):
        return False
    if chat.audience == "sites" and emp.site_code not in (chat.sites or []):
        return False
    return not chat.work_type or chat.work_type == emp.work_type


def _specific(chat: TeamChat) -> tuple:
    """Чем уже круг чата, тем он «роднее» новичку: площадка раньше общего, производство раньше всех."""
    by_site = chat.audience == "sites"
    return (0 if by_site else 1, len(chat.sites or []) if by_site else 0, 0 if chat.work_type else 1, chat.chat_id)


def intro_chat(chats: list[TeamChat], emp, hr_id: int | None) -> TeamChat | None:
    """Основной чат команды для представления: с отметкой «для представлений», иначе чат площадки новичка."""
    pool = [c for c in chats if c.active and c.chat_id != hr_id]
    marked = [c for c in pool if c.for_intro and (c.audience == "none" or matches(c, emp))]
    site = [c for c in pool if c.audience == "sites" and matches(c, emp)]
    found = marked or site
    return min(found, key=_specific) if found else None


async def intro_chat_for(session, emp) -> TeamChat | None:
    return intro_chat(await active_chats(session), emp, await hr_group_id(session))


def _onboarded(emp) -> bool:
    return bool(_data(emp).get("onboarded"))


def _eligible(emp) -> bool:
    """Живой подключившийся новичок: синтетике и не прошедшим знакомство бот ничего не делает."""
    return (not getattr(emp, "is_synthetic", False) and emp.status == "active" and bool(emp.max_user_id)
            and _onboarded(emp))


def pending_chats(chats: list[TeamChat], emp, hr_id: int | None) -> list[TeamChat]:
    done = _data(emp).get(CHATS_KEY) or {}
    return [c for c in chats if c.chat_id != hr_id and matches(c, emp) and str(c.chat_id) not in done]


# --- добавление новичка ------------------------------------------------------------------------

async def _add_one(sender, chat: TeamChat, user_id: int) -> tuple[str | None, str | None]:
    """addMembers для одного чата → ("added" | "failed", код ошибки) или (None, None): сеть или 5xx, позже."""
    try:
        res = await sender.call(chat.chat_id, sender.api.add_members, chat.chat_id, [user_id])
    except MaxApiError as e:
        if e.status >= 500 or e.is_rate_limited:
            log.warning("Чат %s: «МАКС» не ответил (%s), добавим позже", chat.chat_id, e)
            return None, None
        log.info("Чат %s: новичка не добавить: %s", chat.chat_id, e)
        return "failed", f"{e.status} {e.code}".strip()
    except httpx.TransportError as e:
        log.warning("Чат %s: сеть (%s), добавим позже", chat.chat_id, e)
        return None, None
    res = res if isinstance(res, dict) else {}
    for d in res.get("failed_user_details") or []:
        if not d.get("user_ids") or user_id in d["user_ids"]:
            return "failed", str(d.get("error_code") or "add.failed")
    if user_id in (res.get("failed_user_ids") or []):
        return "failed", "add.failed"
    if res.get("success") is False:
        return "failed", str(res.get("message") or "add.failed")[:200]
    return "added", None


def _titles(titles: list[str]) -> str:
    quoted = [f"«{t}»" for t in titles]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " и " + quoted[-1]


def links_text(emp, added: list[str], privacy: bool, links: int = 1) -> str:
    """«Вот чаты твоей команды…»: в какие уже добавил, почему в эти нет, что делать."""
    if added:
        where = ("В чат " if len(added) == 1 else "В чаты ") + _titles(added)
        why = tv(emp, T.LINKS_PARTLY).format(where=where)
    else:
        why = tv(emp, T.LINKS_NONE)
    why += T.LINKS_PRIVACY if privacy else "."
    tail = tv(emp, T.LINKS_TAIL).format(buttons="кнопке" if links == 1 else "кнопкам")
    return " ".join([tv(emp, T.LINKS_HEAD), why, tail])


async def add_to_chats(session, emp, *, sender, repo=None, now: datetime | None = None,
                       chats: list[TeamChat] | None = None) -> dict:
    """Добавить новичка в подходящие чаты, один раз на чат. → {"added": [...], "link": [...], "failed": [...]}.

    Только с первого рабочего дня (по времени новичка) и только живого подключившегося новичка.
    Кого «МАКС» не дал добавить, получают одно сообщение со ссылками-приглашениями на эти чаты.
    Вызывающий держит блокировку новичка (обработчик бота или lock_employee) и коммитит.
    """
    out: dict[str, list[int]] = {"added": [], "link": [], "failed": []}
    if not _eligible(emp) or engine.adaptation_day(emp, now) < 1:
        return out
    hr_id = await hr_group_id(session)
    todo = pending_chats(chats if chats is not None else await active_chats(session), emp, hr_id)
    if not todo:
        return out
    repo = repo or SqlRepo(session)
    results: dict[int, tuple[str, str | None]] = {}
    for chat in todo:
        status, error = await _add_one(sender, chat, emp.max_user_id)
        if status is not None:
            results[chat.chat_id] = (status, error)
    with_link = [c for c in todo if results.get(c.chat_id, ("",))[0] == "failed" and c.link]
    mids = None
    if with_link:
        added = [title_of(c) for c in todo if results.get(c.chat_id, ("",))[0] == "added"]
        privacy = any(results[c.chat_id][1] == PRIVACY for c in with_link)
        kb = keyboard(*[[link(title_of(c)[:64], c.link)] for c in with_link])
        try:
            # без markdown: названия чатов приходят от людей и могут содержать * и _
            mids = await sender.send(repo, links_text(emp, added, privacy, len(with_link)), chat_id=emp.chat_id,
                                     user_id=emp.max_user_id, attachments=kb, employee=emp, fmt=None)
        except Exception as e:  # noqa: BLE001 — итог по чатам всё равно запишем
            log.warning("Ссылки на чаты новичку %s не отправлены: %s", emp.id, e)
    done = dict(_data(emp).get(CHATS_KEY) or {})
    stamp = utcnow().isoformat()
    for chat in todo:
        if chat.chat_id not in results:
            continue
        status, error = results[chat.chat_id]
        entry: dict = {"status": status, "at": stamp, "title": title_of(chat)}
        if status == "failed" and mids and chat in with_link:
            entry.update(status="link", mid=mids[-1])
        if error:
            entry["error"] = error
        done[str(chat.chat_id)] = entry
        out[entry["status"]].append(chat.chat_id)
    _set_data(emp, **{CHATS_KEY: done})
    log.info("Чаты команды, новичок %s: добавлен %s, ссылки %s, не вышло %s",
             emp.id, len(out["added"]), len(out["link"]), len(out["failed"]))
    return out


async def on_onboarded(ctx, repo, emp) -> None:
    """Новичок прошёл знакомство уже в день выхода или позже: добавляем в чаты сразу, не ждём задачу."""
    try:
        await add_to_chats(repo.s, emp, sender=ctx.sender, repo=repo)
    except Exception:  # знакомство важнее: чаты догонит задача team_chats
        log.exception("Чаты команды после знакомства, новичок %s", emp.id)


# --- представление коллегам ------------------------------------------------------------------------

def _intro(emp) -> dict:
    intro = _data(emp).get("intro")
    return intro if isinstance(intro, dict) else {}


def offer_due(emp, now: datetime | None = None) -> bool:
    """Пора спросить про публикацию: рассказ есть, его больше не ждём, про этот рассказ ещё не спрашивали."""
    intro = _intro(emp)
    if not _eligible(emp) or not intro.get("text") or engine.intro_wait(emp, now) is not None:
        return False
    post = _data(emp).get(POST_KEY)
    return not (isinstance(post, dict) and post.get("intro_at") == intro.get("at"))


async def offer_intro(sender, repo, emp, lead: str | None = None) -> bool:
    """«Опубликовать рассказ в чате команды?» с кнопками. lead — «Спасибо…» перед вопросом в том же сообщении.

    False — не спросили: синтетика, рассказа нет, уже спрашивали или чата команды для новичка нет.
    """
    intro = _intro(emp)
    post = _data(emp).get(POST_KEY)
    if getattr(emp, "is_synthetic", False) or not intro.get("text") or \
            (isinstance(post, dict) and post.get("intro_at") == intro.get("at")):
        return False
    chat = await intro_chat_for(repo.s, emp)
    if chat is None:
        return False
    what = tv(emp, T.WHAT_PHOTO if intro.get("photo_url") else T.WHAT_TEXT)
    ask = tv(emp, T.ASK_PUBLISH).format(title=title_of(chat), what=what)
    kb = keyboard([callback(T.PUBLISH_BUTTON, f"ch:pub:{chat.chat_id}")], [callback(T.NO_PUBLISH_BUTTON, "ch:nopub")])
    mids = await sender.send(repo, f"{lead}\n\n{ask}" if lead else ask, chat_id=emp.chat_id,
                             user_id=emp.max_user_id, attachments=kb, employee=emp)
    if not mids:
        return False
    _set_data(emp, **{POST_KEY: {"status": "asked", "chat_id": chat.chat_id, "mid": mids[-1],
                                 "intro_at": intro.get("at"), "at": utcnow().isoformat()}})
    return True


async def _photo(photo: str | None, repo, sender) -> list[dict]:
    """Фото из рассказа: адрес или токен вложения «МАКС»; файл на диске (демо) загружаем."""
    if not photo:
        return []
    if photo.startswith(("http://", "https://")):
        return [{"type": "image", "payload": {"url": photo}}]
    if photo.startswith("/") and Path(photo).is_file():
        try:
            return [await uploads.attachment(photo, "image", repo=repo, sender=sender)]
        except Exception as e:  # noqa: BLE001
            log.warning("Фото %s не загружено: %s", photo, e)
            return []
    return [{"type": "image", "payload": {"token": photo}}]


def intro_post_text(emp) -> str:
    """Подводка «Знакомьтесь, это …» и рассказ новичка дословно."""
    position = (_data(emp).get("position") or "").strip()
    name = display_name(emp)
    lead = (T.INTRO_LEAD_POSITION.format(name=name, position=position, site=emp.site_code) if position
            else T.INTRO_LEAD.format(name=name, site=emp.site_code))
    return f"{lead}\n\n{_intro(emp).get('text', '').strip()}"


async def publish(sender, repo, emp, chat: TeamChat) -> str | None:
    """Рассказ о себе в чат команды: подводка, текст дословно, фото. → mid или None.

    Без markdown: текст новичка — как написал. Фото не прошло — публикуем без него.
    """
    text = intro_post_text(emp)
    photo = await _photo(_intro(emp).get("photo_url"), repo, sender)
    for atts in ([photo, None] if photo else [None]):
        try:
            mids = await sender.send(repo, text, chat_id=chat.chat_id, attachments=atts, fmt=None)
        except Exception as e:  # noqa: BLE001
            log.warning("Представление в чат %s не опубликовано%s: %s", chat.chat_id,
                        " с фото" if atts else "", e)
            continue
        return mids[-1] if mids else None
    return None


# --- кнопки ch: -----------------------------------------------------------------------------------

async def on_button(cb, person) -> None:
    """`ch:hr:<chat_id>` — сделать группу HR-группой; `ch:pub:<chat_id>` / `ch:nopub` — публикация рассказа."""
    parts = cb.payload.split(":")
    kind = parts[1] if len(parts) > 1 else ""
    if kind == "hr":
        await _hr_button(cb, person, parts)
    elif kind in ("pub", "nopub") and person is not None and not cb.is_staff:
        await _post_button(cb, person, kind, parts)
    else:
        await cb.mark()


async def _hr_button(cb, person, parts: list[str]) -> None:
    if not cb.is_staff or getattr(person, "role", None) not in HR_ROLES:
        await cb.toast(T.ONLY_HR)
        return
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        chat_id = cb.chat_id
    if chat_id is None or (cb.chat_id is not None and cb.chat_id != chat_id):
        await cb.mark()
        return
    s = cb.repo.s
    chat = await _get_or_create(s, chat_id)
    chat.active = True
    await set_hr_group(s, chat, sender=cb.ctx.sender, repo=cb.repo, pressed_mid=cb.mid)
    await cb.replace(T.HR_GROUP_DONE, staff=person)


async def _post_button(cb, emp, kind: str, parts: list[str]) -> None:
    post = dict(_data(emp).get(POST_KEY) or {})
    if post.get("status") != "asked":
        await cb.mark()  # уже ответил: старое сообщение просто закрываем
        return
    label = cb.label or (T.PUBLISH_BUTTON if kind == "pub" else T.NO_PUBLISH_BUTTON)
    head = f"{cb.orig_text}\n\n✅ {label}" if cb.orig_text else f"✅ {label}"
    now = utcnow().isoformat()
    if kind == "nopub":
        _set_data(emp, **{POST_KEY: {**post, "status": "declined", "at": now}})
        await cb.replace(f"{head}\n\n{tv(emp, T.NOT_PUBLISHED)}", employee=emp)
        return
    s = cb.repo.s
    hr_id = await hr_group_id(s)
    try:
        chat = await s.get(TeamChat, int(parts[2]))
    except (IndexError, ValueError):
        chat = None
    if chat is None or not chat.active or chat.chat_id == hr_id:
        chat = await intro_chat_for(s, emp)  # чат из вопроса пропал — основной чат сейчас
    mid = await publish(cb.ctx.sender, cb.repo, emp, chat) if chat is not None else None
    if mid is None:
        _set_data(emp, **{POST_KEY: {**post, "status": "failed", "at": now}})
        await cb.replace(f"{head}\n\n{tv(emp, T.PUBLISH_FAILED)}", employee=emp)
        return
    _set_data(emp, **{POST_KEY: {**post, "status": "published", "chat_id": chat.chat_id, "post_mid": mid,
                                 "at": now}})
    await cb.replace(f"{head}\n\n{tv(emp, T.PUBLISHED).format(title=title_of(chat))}", employee=emp)


# --- задача team_chats ---------------------------------------------------------------------------

async def refresh_info(session, sender, *, force: bool = False) -> int:
    """Перечитать название, ссылку и число участников (GET /chats/{id}) у чатов, где это было давно."""
    stale = utcnow() - INFO_TTL
    n = 0
    for chat in await active_chats(session):
        if force or chat.info_at is None or chat.info_at < stale:
            info = await chat_info(sender, chat.chat_id)
            if info is not None:
                apply_info(chat, info)
                n += 1
    return n


def _candidates():
    return (select(Employee).options(selectinload(Employee.site))
            .where(Employee.status == "active", Employee.is_synthetic.is_(False), Employee.max_user_id.is_not(None),
                   Employee.dialog_data["onboarded"].as_boolean().is_(True))
            .order_by(Employee.id))


async def tick(now: datetime | None = None, *, sender=None, session_factory=None) -> dict:
    """Задача team_chats: сведения о чатах, добавление новичков в первый день, вопрос о публикации рассказа.

    now — для дня адаптации (тесты); сведения о чатах — по настоящему времени.
    """
    stats = {"refreshed": 0, "added": 0, "link": 0, "failed": 0, "offers": 0}
    if sender is None:
        sender = _sender()
        if sender is None:
            return stats  # бот выключен: новичков добавим, когда он запустится
    if session_factory is None:
        from app.db.session import async_session as session_factory
    async with session_factory() as s:
        await ensure_hr_row(s)
        stats["refreshed"] = await refresh_info(s, sender)
        await s.commit()
        chats = await active_chats(s)
        hr_id = await hr_group_id(s)
        if not [c for c in chats if c.chat_id != hr_id]:
            return stats
        emps = (await s.scalars(_candidates())).all()
        todo = [e.id for e in emps if offer_due(e, now) or
                (engine.adaptation_day(e, now) >= 1 and pending_chats(chats, e, hr_id))]
    for emp_id in todo:
        async with session_factory() as s:
            try:
                emp = await s.scalar(select(Employee).options(selectinload(Employee.site))
                                     .where(Employee.id == emp_id))
                if emp is None:
                    continue
                await lock_employee(s, emp)  # бот мог поменять dialog_data: дальше работаем со свежими данными
                repo = SqlRepo(s)
                res = await add_to_chats(s, emp, sender=sender, repo=repo, now=now)
                for k in ("added", "link", "failed"):
                    stats[k] += len(res[k])
                if offer_due(emp, now) and await offer_intro(sender, repo, emp):
                    stats["offers"] += 1
                await s.commit()
            except Exception:
                await s.rollback()
                log.exception("team_chats: новичок %s", emp_id)
    return stats


# --- карточка новичка в Mini App ---------------------------------------------------------------------

def reason(error: str | None) -> str | None:
    if not error:
        return None
    return T.ERRORS.get(error, T.ERROR_OTHER)


async def card(session, emp) -> list[dict]:
    """Чаты новичка для карточки: [{chat_id, title, status, at, reason, link, intro}].

    status: added — добавлен, link — отправили ссылку, failed — не добавили и ссылки нет,
    pending — добавим в первый рабочий день, intro — только опубликовали представление.
    intro — в этом чате опубликован рассказ о себе. Синтетике бот ничего не делает: список пустой.
    """
    if getattr(emp, "is_synthetic", False):
        return []
    rows = {c.chat_id: c for c in (await session.scalars(select(TeamChat))).all()}
    hr_id = await hr_group_id(session)
    data = _data(emp)
    post = data.get(POST_KEY) if isinstance(data.get(POST_KEY), dict) else {}
    posted = post.get("chat_id") if post.get("status") == "published" else None
    out = []
    for key, entry in (data.get(CHATS_KEY) or {}).items():
        try:
            cid = int(key)
        except ValueError:
            continue
        chat = rows.get(cid)
        out.append({"chat_id": cid, "title": title_of(chat) if chat else (entry.get("title") or title_of(None)),
                    "status": entry.get("status") or "failed", "at": entry.get("at"),
                    "reason": reason(entry.get("error")) if entry.get("status") != "added" else None,
                    "link": chat.link if chat else None, "intro": cid == posted})
    seen = {x["chat_id"] for x in out}
    for chat in pending_chats([c for c in rows.values() if c.active], emp, hr_id):
        out.append({"chat_id": chat.chat_id, "title": title_of(chat), "status": "pending", "at": None,
                    "reason": None, "link": chat.link, "intro": chat.chat_id == posted})
        seen.add(chat.chat_id)
    if posted is not None and posted not in seen:
        out.append({"chat_id": posted, "title": title_of(rows.get(posted)), "status": "intro", "at": post.get("at"),
                    "reason": None, "link": rows[posted].link if posted in rows else None, "intro": True})
    return out


async def chat_stats(session) -> dict[int, dict[str, int]]:
    """Сколько новичков бот добавил в каждый чат и скольким отправил ссылку: {chat_id: {added, link}}."""
    out: dict[int, dict[str, int]] = {}
    rows = await session.scalars(select(Employee.dialog_data).where(Employee.dialog_data.has_key(CHATS_KEY)))
    for data in rows:
        for key, entry in ((data or {}).get(CHATS_KEY) or {}).items():
            try:
                cid = int(key)
            except ValueError:
                continue
            st = (entry or {}).get("status")
            if st in ("added", "link"):
                out.setdefault(cid, {"added": 0, "link": 0})[st] += 1
    return out
