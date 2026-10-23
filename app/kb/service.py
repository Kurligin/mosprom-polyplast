"""Ответ на свободный вопрос новичка (hooks.on_free_text) и кнопка «Связаться с HR» (kb:hr:<id>).

Маршрут:
1. Личный кадровый случай (правила) → обращение HR без ИИ.
2. Гибридный поиск → пункт выше порога или согласие обоих поисков → ответ сразу.
3. Между порогами → GigaChat выбирает пункт из топ-5 (без КТ, вопрос замаскирован).
4. Ниже порога или «нет подходящего» → «не знаю, передал HR» и обращение.
Всё пишется в questions_log. ИИ не пишет норму: в ответе дословный пункт с источником.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select

from app.bot import hooks
from app.bot import texts as T
from app.bot.keyboards import callback, keyboard
from app.db.models import HrAnswer, KbChunk, KbDocument, Message, QuestionLog
from app.kb.card import render_card, shown_text
from app.kb.query import topic_of
from app.kb.route import agree_direct, is_personal_hr_case, kb_config, plan, thresholds
from app.kb.search import DRAFT_LABEL, Hit, search

log = logging.getLogger(__name__)

# --- тексты: коротко, по-человечески, без длинных тире ---

PERSONAL = (
    "Это личный вопрос, его лучше решить с HR. Я уже передал, ответ придёт сюда. Вопрос увидит только HR.",
    "Это личный вопрос, его лучше решить с HR. Я уже передал, ответ придёт сюда. Вопрос увидит только HR.",
)
UNKNOWN = (
    "Честно, не знаю. В документах ответа не нашёл, поэтому передал вопрос HR. Ответ придёт сюда.",
    "Честно, не знаю. В документах ответа не нашёл, поэтому передал вопрос HR. Ответ придёт сюда.",
)
FORWARDED = ("Передал вопрос HR. Ответ придёт сюда.", "Передал вопрос HR. Ответ придёт сюда.")
LEAD_DOC = ("Нашёл в документах компании. Смотри:", "Нашёл в документах компании. Смотрите:")
LEAD_ARTICLE = ("Держи подсказку:", "Вот подсказка:")
LEAD_HR = ("HR уже отвечали на такой вопрос:", "HR уже отвечали на такой вопрос:")
LEAD_DRAFT = (
    "Нашёл статью HR. Она пока черновик, так что если что-то не сходится, уточни у HR:",
    "Нашёл статью HR. Она пока черновик, так что если что-то не сходится, уточните у HR:",
)
KT_NOTE = (
    "Это из документа с грифом КТ, не пересылай его за пределы компании.",
    "Это из документа с грифом КТ, не пересылайте его за пределы компании.",
)
THANKS = ("Пожалуйста! Если появится ещё вопрос, пиши.", "Пожалуйста! Если появится ещё вопрос, пишите.")
HELLO = (
    "Привет! Напиши вопрос, я поищу ответ в документах компании.",
    "Здравствуйте! Напишите вопрос, я поищу ответ в документах компании.",
)
HR_BUTTON = "Связаться с HR"
NOT_FOUND = "Не нашёл этот вопрос"

_THANKS_RE = re.compile(r"^(спасибо|спс|благодарю|пасиб\w*|сенкс)[\s!.)🙏👍]*$", re.IGNORECASE)
_HELLO_RE = re.compile(r"^(привет\w*|здравствуй\w*|добрый (день|вечер|утро)|доброе утро|хай|ку)[\s!.,)👋]*$",
                       re.IGNORECASE)
_ACK_RE = re.compile(r"^(ок|окей|ok|понял|поняла|понятно|ясно|хорошо|ладно|угу|ага|👍|👌)[\s!.)]*$", re.IGNORECASE)


# --- выбор ответа (общий код с eval) ---


async def pick_answer(question: str, hits: list[Hit], *, names: list[str] | None = None,
                      on_llm=None) -> tuple[Hit | None, str]:
    """→ (пункт или None, как выбран): direct | llm | fallback | llm_none | low.

    Если GigaChat недоступен, отвечаем лучшим пунктом только при близости не ниже llm_fallback_threshold.
    """
    from app.llm.gigachat import LlmUnavailable, choose_chunk

    hi, lo = thresholds()
    action, hs = plan(hits, hi, lo, agree_direct=agree_direct())
    if action == "answer":
        return hs[0], "direct"
    if action != "ask_llm":
        return None, "low"
    if on_llm is not None:
        await on_llm()
    cands = [{"chunk_id": h.chunk_id, "text": shown_text(h.text, question, h.focus)} for h in hs]
    try:
        cid = await choose_chunk(question, cands, names, raise_errors=True)
    except LlmUnavailable:
        fallback = float(kb_config().get("llm_fallback_threshold", 0.6))
        top = hits[0]
        if (top.sim or 0.0) >= fallback and not top.is_kt:
            return top, "fallback"
        return None, "llm_down"
    chosen = next((h for h in hs if h.chunk_id == cid), None)
    return (chosen, "llm") if chosen else (None, "llm_none")


# --- карточка ответа ---


def _lead(emp, hit: Hit) -> str:
    if hit.kind == "hr_answer":
        return T.tv(emp, LEAD_HR)
    if hit.is_draft:
        return T.tv(emp, LEAD_DRAFT)
    if hit.kind in ("scenario", "article"):
        return T.tv(emp, LEAD_ARTICLE)
    return T.tv(emp, LEAD_DOC)


def answer_card(emp, hit: Hit, question: str) -> str:
    """Одно сообщение: подводка, дословная цитата, «Источник: …» (и пометка КТ)."""
    card = render_card(hit.text, hit.source_label, question, lead=_lead(emp, hit), focus=hit.focus)
    if hit.is_kt:
        card += "\n\n" + T.tv(emp, KT_NOTE)
    return card


def topic_for(question: str, hit: Hit | None) -> str | None:
    """Тема для статистики: группа синонимов вопроса, иначе раздел или статья найденного пункта."""
    t = topic_of(question)
    if t or hit is None:
        return t
    if hit.kind in ("scenario", "article"):
        return hit.path[:64] or None
    parts = [p.strip() for p in (hit.path or "").split("→")]
    for p in parts[1:2] + parts[:1]:
        m = re.match(r"^(?:\d+(?:\.\d+)*\.?\s+|Слайд\s+\d+\.?\s*)(.+)$", p)
        if m and m.group(1).strip():
            return m.group(1).strip()[:64]
    return None


# --- обработчики бота ---


async def _send(ctx, repo, emp, text: str, attachments=None, ticket_id=None):
    return await ctx.sender.send(repo, text, chat_id=emp.chat_id, user_id=emp.max_user_id, attachments=attachments,
                                 employee=emp, ticket_id=ticket_id)


async def _typing(ctx, emp) -> None:
    typing = getattr(ctx.sender, "typing", None)
    if typing is not None:
        await typing(emp.chat_id)


async def _log(repo, emp, question: str, *, route: str, hit: Hit | None = None, score: float | None = None,
               ticket=None, topic: str | None = None) -> QuestionLog:
    row = QuestionLog(employee_id=emp.id, question=question, route=route,
                      chunk_id=hit.chunk_id if hit else None, score=score, topic=topic,
                      ticket_id=getattr(ticket, "id", None))
    repo.s.add(row)
    await repo.s.flush()
    return row


def _small_talk(emp, text: str) -> str | None:
    """Спасибо / привет / «ок» — не вопрос. → ответ, "" (промолчать) или None (это вопрос)."""
    t = text.strip()
    if _THANKS_RE.match(t):
        return T.tv(emp, THANKS)
    if _HELLO_RE.match(t):
        return T.tv(emp, HELLO)
    if _ACK_RE.match(t):
        return ""
    return None


async def answer_question(ctx, repo, emp, text: str) -> None:
    """Свободный вопрос новичка → ответ по документам, выбор пункта в GigaChat или обращение к HR."""
    from app.bot.handlers import create_hr_ticket

    question = " ".join((text or "").split())
    if not question:
        return
    small = _small_talk(emp, question)
    if small is not None:
        if small:
            await _send(ctx, repo, emp, small)
        return

    if is_personal_hr_case(question):
        ticket = await create_hr_ticket(ctx, repo, emp, question, source="personal", is_personal=True)
        await _log(repo, emp, question, route="personal", ticket=ticket, topic=topic_of(question))
        await _send(ctx, repo, emp, T.tv(emp, PERSONAL), ticket_id=getattr(ticket, "id", None))
        return

    await _typing(ctx, emp)
    hits = await search(repo.s, question, site_code=emp.site_code, limit=5)
    best = max((h.sim or 0.0 for h in hits), default=None)
    names = [n for n in (getattr(emp, "full_name", None), getattr(emp, "first_name", None)) if n]
    chosen, how = await pick_answer(question, hits, names=names, on_llm=lambda: _typing(ctx, emp))
    log.info("kb: вопрос сотрудника %s → %s (близость %.3f)", emp.id, how, best or 0.0)

    if chosen is None:
        ticket = await create_hr_ticket(ctx, repo, emp, question, source="unknown")
        await _log(repo, emp, question, route="unknown", score=best, ticket=ticket, topic=topic_of(question))
        await _send(ctx, repo, emp, T.tv(emp, UNKNOWN), ticket_id=getattr(ticket, "id", None))
        return

    row = await _log(repo, emp, question, route="answer", hit=chosen, score=chosen.sim,
                     topic=topic_for(question, chosen))
    if (chosen.chunk_key or "").startswith("scenario:food"):
        # «где пообедать» — сразу подборка заведений площадки, если модуль сценария её умеет
        food = hooks.command_handler("food")
        if food is not None:
            await food(ctx, repo, emp, "")
            return
    await _send(ctx, repo, emp, answer_card(emp, chosen, question),
                keyboard([callback(HR_BUTTON, f"kb:hr:{row.id}")]))


async def _card_from_log(session, emp, ql: QuestionLog, mid: str | None) -> tuple[str, str]:
    """Текст карточки (как отправили) и подпись источника для обращения."""
    label = ""
    if ql.chunk_id:
        ch = await session.get(KbChunk, ql.chunk_id)
        if ch is not None:
            doc = await session.get(KbDocument, ch.document_id) if ch.document_id else None
            hra = await session.get(HrAnswer, ch.hr_answer_id) if ch.hr_answer_id else None
            status = (hra.status if hra else (doc.status if doc else "confirmed")) or "confirmed"
            label = DRAFT_LABEL if (status != "confirmed" and not hra) else (ch.source_label or "")
            if mid:
                sent = (await session.execute(
                    select(Message.text).where(Message.mid == mid, Message.direction == "out")
                    .order_by(Message.id.desc()).limit(1))).scalar()
                if sent:
                    return sent, label
            kind = "hr_answer" if hra else (doc.doc_type if doc and doc.doc_type in ("scenario", "article")
                                             else "doc")
            hit = Hit(chunk_id=ch.id, text=ch.text, source_label=label, score=0.0, is_kt=ch.is_kt, status=status,
                      chunk_key=ch.chunk_key, kind=kind, path=ch.path or "")
            return answer_card(emp, hit, ql.question), label
    return "", label


async def on_button(cb: hooks.Callback, person) -> None:
    """kb:hr:<question_log_id> → обращение с этим вопросом; карточка меняется на месте."""
    from app.bot.handlers import create_hr_ticket

    parts = cb.payload.split(":")
    if cb.is_staff or len(parts) < 3 or parts[1] != "hr" or not parts[2].isdigit():
        await cb.toast("Кнопка устарела")
        return
    emp, session = person, cb.repo.s
    ql = await session.get(QuestionLog, int(parts[2]))
    if ql is None or ql.employee_id != emp.id:
        await cb.toast(NOT_FOUND)
        return
    card, label = await _card_from_log(session, emp, ql, cb.mid)
    if ql.ticket_id is None:
        note = f"\n\n(Корсик показал: {label}. Новичку этого мало, просит связаться с HR.)" if label else ""
        ticket = await create_hr_ticket(cb.ctx, cb.repo, emp, ql.question + note, source="button")
        ql.ticket_id = getattr(ticket, "id", None)
        ql.route = "hr"
        await session.flush()
    done = T.tv(emp, FORWARDED)
    await cb.replace(f"{card}\n\n{done}" if card else done, employee=emp)
