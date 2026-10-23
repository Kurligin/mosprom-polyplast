"""Движок опросов: один опрос — одно сообщение, которое меняется на месте.

- start_survey шлёт первый вопрос новым сообщением; ответ кнопкой превращает то же сообщение в следующий вопрос.
- Свой вариант, «что мешает?», открытый вопрос: ждём текст (dialog_state "sv:<код>:<вопрос>:<режим>").
  Текст приходит новым сообщением, поэтому старое сообщение остаётся с отметкой выбора без кнопок,
  а следующий вопрос идёт новым сообщением.
- Состояние прохождения — emp.dialog_data["survey"], история — emp.dialog_data["surveys"][код].
- Кнопки: sv:<код>:<вопрос>:<действие>[:<аргумент>] и sv:<код>:go («Продолжить опрос» из напоминания).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.bot import runtime
from app.bot.keyboards import callback, keyboard
from app.bot.repo import lock_employee
from app.bot.texts import tv
from app.config import settings
from app.db.models import SurveyAnswer
from app.scenario.workcal import is_workday
from app.surveys import config as C
from app.surveys import store
from app.surveys import texts as T

log = logging.getLogger(__name__)

PREFIX = "sv"
STATE_KEY = "survey"
HISTORY_KEY = "surveys"
TEXT_WAIT_TTL = timedelta(hours=12)   # дольше ждать текст не будем: дальше это просто вопрос в бот
REMIND_HOUR = 10                       # напоминания — не раньше 10:00 по времени площадки
STALE_DAYS = 3                         # опросу больше 3 рабочих дней — не напоминаем

ACT = {"recruiter": "rc", "hr": "hc", "forward": "fw", "forward_hr": "fh", "remind_access": "acc"}
_X = re.compile(r"(?<![А-Яа-яЁёA-Za-z])Х(?![А-Яа-яЁёA-Za-z])")


# --- общее -------------------------------------------------------------------------

def survey_for_step(step_number) -> str | None:
    """Код опроса для шага сценария (номер строки 1..68) или None."""
    try:
        n = int(step_number)
    except (TypeError, ValueError):
        return None
    return C.step_map().get(n)


def emp_now(emp, now: datetime | None = None) -> datetime:
    """«Сейчас» для новичка с учётом машины времени."""
    return (now or datetime.now(UTC)) + timedelta(hours=getattr(emp, "time_shift_hours", 0) or 0)


def _state(emp) -> dict | None:
    st = (emp.dialog_data or {}).get(STATE_KEY)
    return dict(st) if isinstance(st, dict) else None


def _history(emp) -> dict:
    h = (emp.dialog_data or {}).get(HISTORY_KEY)
    return dict(h) if isinstance(h, dict) else {}


def _save(emp, st: dict | None, *, new_run: bool = False, **history) -> None:
    """Новый dict целиком, чтобы SQLAlchemy заметил изменение JSONB. new_run — история опроса заново."""
    data = dict(emp.dialog_data or {})
    data[STATE_KEY] = st
    if history and st:
        h = _history(emp)
        h[st["code"]] = {**({} if new_run else (h.get(st["code"]) or {})), **history}
        data[HISTORY_KEY] = h
    emp.dialog_data = data


def _set_wait(emp, st: dict, mode: str | None) -> None:
    st["wait"] = mode
    if mode:
        st["wait_at"] = datetime.now(UTC).isoformat()
        emp.dialog_state = f"{PREFIX}:{st['code']}:{st['q']}:{mode}"[:64]
    else:
        st.pop("wait_at", None)
        if (emp.dialog_state or "").split(":", 1)[0] == PREFIX:
            emp.dialog_state = None


def _name(text: str | None, emp) -> str | None:
    if not text:
        return text
    return _X.sub(getattr(emp, "first_name", "") or "", text)


def _join(*parts) -> str:
    return "\n\n".join(p for p in parts if p)


def _site(emp) -> str | None:
    return getattr(emp, "site_code", None)


def pending_surveys(emp) -> list[str]:
    """Начатые и не пройденные опросы новичка (для карточки в Mini App)."""
    st = _state(emp)
    return [st["code"]] if st and not st.get("done") else []


def survey_history(emp) -> dict:
    """{код: {started_at, done_at?, reminded?, closed_at?}} — для карточки и отладки."""
    return _history(emp)


# --- отображение -----------------------------------------------------------------

def _head(qs: list, i: int) -> str | None:
    return T.QUESTION_HEAD.format(n=i + 1, total=len(qs)) if len(qs) > 1 else None


def _body(q: C.Question) -> str:
    return q.text + (f"\n({q.legend})" if q.legend else "")


def _question_marked(emp, sv: C.Survey, i: int, mark: str) -> str:
    qs = sv.visible(_site(emp))
    head = _head(qs, i)
    body = _name(_body(qs[i]), emp)
    return (f"{head}\n{body}" if head else body) + f"\n\n✅ {mark}"


def _btn(text: str, code: str, i: int, act: str, arg=None) -> dict:
    payload = f"{PREFIX}:{code}:{i}:{act}" + (f":{arg}" if arg is not None else "")
    return callback(text, payload[:128])


def _question_kb(emp, sv: C.Survey, i: int, q: C.Question, sel: list[int] | None = None) -> list[dict]:
    code = sv.code
    if q.kind == "scale":
        return keyboard([_btn(str(n), code, i, "s", n) for n in range(1, 6)],
                        [_btn(str(n), code, i, "s", n) for n in range(6, 11)])
    if q.kind == "open":
        return keyboard([_btn(T.BTN_SKIP, code, i, "sk")])
    opts = q.visible_options(_site(emp))
    if q.kind == "yes_no":
        return keyboard([_btn(o.label, code, i, "o", j) for j, o in enumerate(opts)])
    if q.kind == "multi":
        chosen = set(sel or [])
        rows = [[_btn((T.MULTI_MARK if j in chosen else "") + o.label, code, i, "m", j)] for j, o in enumerate(opts)]
        rows.append([_btn(T.BTN_DONE, code, i, "md")])
        return keyboard(*rows)
    return keyboard(*[[_btn(o.label, code, i, "o", j)] for j, o in enumerate(opts)])


def question_view(emp, sv: C.Survey, i: int, *, prefix: str | None = None,
                  sel: list[int] | None = None) -> tuple[str, list[dict]]:
    """Текст и кнопки вопроса i (по списку вопросов для площадки новичка)."""
    qs = sv.visible(_site(emp))
    q = qs[i]
    head = _head(qs, i)
    body = _name(_body(q), emp)
    intro = _name(sv.intro, emp) if i == 0 else None
    text = _join(prefix, intro, f"{head}\n{body}" if head else body)
    if q.kind == "open":
        text = _join(text, tv(emp, T.OPEN_HINT))
    return text, _question_kb(emp, sv, i, q, sel)


def _finish_text(emp, sv: C.Survey, prefix: str | None) -> str:
    outro = tv(emp, T.THANKS) if sv.outro is None else _name(sv.outro, emp)
    return _join(prefix, outro) or tv(emp, T.THANKS)


def _is_last(emp, sv: C.Survey, i: int) -> bool:
    return i >= len(sv.visible(_site(emp))) - 1


def _mark_done(emp, st: dict) -> None:
    if not st.get("done"):
        st["done"] = True
        h = _history(emp).get(st["code"]) or {}
        st["_done_at"] = h.get("done_at") or emp_now(emp).isoformat()


def _advance(emp, sv: C.Survey, st: dict, *, prefix: str | None = None) -> tuple[str, list[dict]]:
    """Следующий вопрос (или финальное «спасибо») с подводкой prefix."""
    qs = sv.visible(_site(emp))
    _set_wait(emp, st, None)
    st["sel"] = []
    nxt = st["q"] + 1
    if nxt < len(qs):
        st["q"] = nxt
        st["answer_id"] = None
        text, kb = question_view(emp, sv, nxt, prefix=prefix)
        if qs[nxt].kind == "open":
            _set_wait(emp, st, "open")
        return text, kb
    st["q"] = len(qs)
    _mark_done(emp, st)
    return _finish_text(emp, sv, prefix), []


def _interjection(emp, sv: C.Survey, st: dict, i: int, opt: C.Option, mark: str):
    """После выбора: ответ Корсика из ТЗ, кнопки-действия, ожидание текста. None — сразу дальше."""
    code = sv.code
    actions = [[_btn(a.label, code, i, ACT[a.do])] for a in opt.actions]
    skip = [[_btn(T.BTN_SKIP, code, i, "sk")]]
    reply = _name(opt.reply, emp)
    if opt.ask in ("why", "problem"):
        # ответ придёт новым сообщением, это останется в чате: вопрос, отметка выбора и вопрос Корсика
        return _join(_question_marked(emp, sv, i, mark), reply), keyboard(*actions, *skip), opt.ask
    if opt.ask == "reason":
        return _join(_question_marked(emp, sv, i, mark), reply, tv(emp, T.REASON)), keyboard(*actions, *skip), "reason"
    if opt.ask == "other":
        return _join(_question_marked(emp, sv, i, mark), tv(emp, T.OTHER)), keyboard(*actions, *skip), "other"
    if actions:
        rows = list(actions)
        if not _is_last(emp, sv, i):
            rows.append([_btn(T.BTN_NEXT, code, i, "n")])
        elif sv.outro != "":
            rows.append([_btn(T.BTN_DONE, code, i, "n")])
        return reply or _question_marked(emp, sv, i, mark), keyboard(*rows), None
    return None


# --- запись ответов ----------------------------------------------------------------

async def _question_id(sv: C.Survey, q: C.Question) -> int:
    ids = await store.question_ids()
    return ids[sv.code][q.key]


async def _record(repo, emp, sv: C.Survey, st: dict, i: int, q: C.Question, *, value: str | None = None,
                  score: int | None = None, mood: str | None = None, text: str | None = None) -> SurveyAnswer:
    ans = SurveyAnswer(employee_id=emp.id, question_id=await _question_id(sv, q), value=(value or None) and value[:255],
                       score=score, text=text, topic=q.topic, mood=mood,
                       is_synthetic=bool(getattr(emp, "is_synthetic", False)))
    repo.s.add(ans)
    await repo.s.flush()
    st["answer_id"] = ans.id
    st["value"] = value
    if _is_last(emp, sv, i):
        _mark_done(emp, st)
    return ans


async def _attach_text(repo, emp, sv: C.Survey, st: dict, q: C.Question, text: str) -> SurveyAnswer:
    ans = await repo.s.get(SurveyAnswer, st["answer_id"]) if st.get("answer_id") else None
    if ans is None:
        return await _record(repo, emp, sv, st, st["q"], q, text=text)
    ans.text = f"{ans.text}\n{text}" if ans.text else text
    await repo.s.flush()
    return ans


async def _option_flags(repo, emp, sv: C.Survey, q: C.Question, opts: list[C.Option], ans) -> list:
    from app.signals import service as signals

    flags = []
    for o in opts:
        if not o.flag:
            continue
        quote = f"«{o.label}». Опрос «{sv.title}», вопрос «{_name(q.text, emp)}»."
        flag = await signals.raise_flag(repo, emp, level=o.flag["level"], topic=o.flag["topic"], quote=quote,
                                        source="button", answer=ans)
        flags.append(flag)
    return flags


def _mood_for_score(n: int) -> str:
    return "positive" if n >= 8 else "neutral" if n >= 5 else "negative"


def _mood_for(opts: list[C.Option]) -> str | None:
    moods = [o.mood for o in opts if o.mood]
    for m in ("negative", "neutral", "positive"):
        if m in moods:
            return m
    return None


# --- обращения из опроса -------------------------------------------------------------

async def _ticket(ctx, repo, emp, text: str, *, category: str | None):
    from app.bot.handlers import create_hr_ticket

    try:
        return await create_hr_ticket(ctx, repo, emp, text, "survey", category=category)
    except Exception:  # опрос не должен ломаться, если HR-группа недоступна
        log.exception("Не удалось создать обращение из опроса")
        return None


async def _remind_access(ctx, repo, emp) -> str:
    """Кнопка «Напомнить руководителю про доступы». Без модуля руководителя — обращение HR."""
    try:
        from app.manager import service as manager
    except ImportError:
        manager = None
    ok = False
    if manager is not None and hasattr(manager, "remind_access"):
        try:
            ok = bool(await manager.remind_access(repo, emp))
        except Exception:
            log.exception("remind_access")
    if ok:
        return tv(emp, T.ACCESS_REMINDED)
    await _ticket(ctx, repo, emp, T.TICKET_ACCESS, category="доступы")
    return tv(emp, T.ACCESS_TO_HR)


# --- картинка на карточке опроса --------------------------------------------------------

ASSETS = Path(__file__).resolve().parents[2] / "assets"


async def _image(repo, sv: C.Survey) -> dict | None:
    """Картинка шага из ТЗ (assets/) как вложение; без файла или бота — None, опрос идёт без неё."""
    ctx = runtime.get_ctx()
    if not sv.image or ctx is None:
        return None
    path = ASSETS / sv.image
    if not path.exists():
        return None
    try:
        from app.bot import uploads

        return await uploads.attachment(path, repo=repo, sender=ctx.sender)
    except Exception as e:  # noqa: BLE001 — без картинки опрос всё равно идёт
        log.warning("Картинка опроса %s не загружена: %s", sv.code, e)
        return None


def _atts(st: dict, mid: str | None, kb: list[dict]) -> list[dict]:
    """Картинка остаётся только у первого сообщения опроса."""
    img = st.get("img")
    return ([img] if img and mid and mid == st.get("img_mid") else []) + list(kb or [])


# --- запуск ----------------------------------------------------------------------------

async def start_survey(repo, emp, code: str, *, force: bool = False) -> str | None:
    """Первый вопрос новым сообщением. Повторный вызов для начатого или пройденного опроса ничего не шлёт.

    Тестовый новичок (is_test) может пройти пройденный опрос заново: машина времени перематывает дни назад.
    Возвращает mid сообщения опроса (или mid уже идущего опроса), None — опрос не начат.
    """
    sv = C.get(code)
    if sv is None:
        log.warning("Опрос %s не найден в config/surveys.yaml", code)
        return None
    qs = sv.visible(_site(emp))
    if not qs:
        return None
    # планировщик: бот или API могли поменять dialog_data, пока мы работали — читаем свежие под блокировкой
    await lock_employee(getattr(repo, "s", None), emp)
    st = _state(emp)
    if st and st.get("code") == code and not st.get("done") and not force:
        return st.get("mid")
    if (_history(emp).get(code) or {}).get("started_at") and not (force or getattr(emp, "is_test", False)):
        return None
    if not (getattr(emp, "chat_id", None) or getattr(emp, "max_user_id", None)):
        log.info("Опрос %s: новичок %s не подключён к боту", code, emp.id)
        return None
    await store.question_ids()  # таблицы опросов в БД до первого ответа
    if st and not st.get("done"):
        await _close(repo, emp, st)

    st = {"code": code, "q": 0, "mid": None, "started_at": emp_now(emp).isoformat(), "reminded": 0,
          "done": False, "sel": [], "answer_id": None, "wait": None}
    text, kb = question_view(emp, sv, 0)
    if qs[0].kind == "open":
        _set_wait(emp, st, "open")
    elif (emp.dialog_state or "").split(":", 1)[0] == PREFIX:
        emp.dialog_state = None
    img = await _image(repo, sv)
    mids = await runtime.send(repo, text, employee=emp, attachments=([img] if img else []) + kb)
    st["mid"] = mids[-1] if mids else None
    if img and st["mid"]:
        st["img"], st["img_mid"] = img, st["mid"]
    st["view"] = {"text": text, "kb": kb, "wait": st.get("wait")}
    _save(emp, st, new_run=True, started_at=st["started_at"])
    log.info("Опрос %s начат у новичка %s", code, emp.id)
    return st["mid"]


async def _close(repo, emp, st: dict) -> None:
    """Незаконченный опрос уступает место новому: кнопки у старого сообщения убираем."""
    mid = st.get("mid")
    view = st.get("view") or {}
    if mid and view.get("text"):
        await runtime.edit(mid, view["text"], _atts(st, mid, []), chat_id=getattr(emp, "chat_id", None))
    _set_wait(emp, st, None)
    _save(emp, st, closed_at=emp_now(emp).isoformat())


def reset(emp, code: str | None = None) -> None:
    """Для демо: забыть прохождение опроса (или всех), чтобы его можно было начать заново."""
    data = dict(emp.dialog_data or {})
    h = dict(data.get(HISTORY_KEY) or {})
    if code is None:
        h = {}
    else:
        h.pop(code, None)
    st = data.get(STATE_KEY)
    if isinstance(st, dict) and (code is None or st.get("code") == code):
        data[STATE_KEY] = None
        if (emp.dialog_state or "").split(":", 1)[0] == PREFIX:
            emp.dialog_state = None
    data[HISTORY_KEY] = h
    emp.dialog_data = data


# --- кнопки ----------------------------------------------------------------------------

async def _show(cb, emp, st: dict, text: str, kb: list[dict]) -> None:
    # режим ожидания текста — вместе с видом: «Продолжить опрос» вернёт и вопрос, и ожидание ответа
    st["view"] = {"text": text, "kb": kb, "wait": st.get("wait")}
    history = {}
    if st.get("_done_at"):
        history["done_at"] = st.pop("_done_at")
    _save(emp, st, **history)
    await cb.replace(text, _atts(st, cb.mid, kb), employee=emp)


async def handle_callback(cb, person) -> None:
    if cb.is_staff or person is None:
        await cb.toast(T.NOT_FOR_STAFF)
        return
    emp = person
    parts = cb.payload.split(":")
    sv = C.get(parts[1]) if len(parts) > 2 else None
    if sv is None:
        await cb.toast(T.STALE)
        return
    st = _state(emp)
    if parts[2] == "go":
        await _continue(cb, emp, sv, st)
        return
    try:
        i = int(parts[2])
        act = parts[3]
    except (IndexError, ValueError):
        await cb.toast(T.STALE)
        return
    arg = parts[4] if len(parts) > 4 else None
    qs = sv.visible(_site(emp))
    if not (st and st.get("code") == sv.code and st.get("q") == i and i < len(qs)):
        await _stale_action(cb, emp, sv, i, act)
        return
    st["mid"] = cb.mid or st.get("mid")
    try:
        await _dispatch(cb, emp, sv, st, i, qs[i], act, arg)
    except (IndexError, ValueError, TypeError) as e:
        log.warning("Кнопка опроса %s: %s", cb.payload, e)
        if not cb.answered:
            await cb.toast(T.STALE)


async def _dispatch(cb, emp, sv: C.Survey, st: dict, i: int, q: C.Question, act: str, arg: str | None) -> None:
    repo, ctx = cb.repo, cb.ctx
    if act == "o":
        opts = q.visible_options(_site(emp))
        opt = opts[int(arg)]
        ans = await _record(repo, emp, sv, st, i, q, value=opt.label, mood=opt.mood)
        await _option_flags(repo, emp, sv, q, [opt], ans)
        await _after_choice(cb, emp, sv, st, i, opt, opt.label)
    elif act == "s":
        n = max(1, min(10, int(arg)))
        await _record(repo, emp, sv, st, i, q, value=str(n), score=n, mood=_mood_for_score(n))
        if q.reason_below and n < q.reason_below:
            _set_wait(emp, st, "reason")
            text = _join(_question_marked(emp, sv, i, str(n)), tv(emp, T.REASON))
            await _show(cb, emp, st, text, keyboard([_btn(T.BTN_SKIP, sv.code, i, "sk")]))
        else:
            await _show(cb, emp, st, *_advance(emp, sv, st))
    elif act == "m":
        opts = q.visible_options(_site(emp))
        j = int(arg)
        sel = set(st.get("sel") or [])
        if j in sel:
            sel.discard(j)
        elif opts[j].exclusive:
            sel = {j}
        else:
            sel = {k for k in sel if not opts[k].exclusive} | {j}
        st["sel"] = sorted(sel)
        text = (st.get("view") or {}).get("text") or question_view(emp, sv, i)[0]
        await _show(cb, emp, st, text, _question_kb(emp, sv, i, q, st["sel"]))
    elif act == "md":
        opts = q.visible_options(_site(emp))
        chosen = [opts[k] for k in st.get("sel") or [] if k < len(opts)]
        if not chosen:
            await cb.toast(T.MULTI_EMPTY)
            return
        value = "; ".join(o.label for o in chosen)
        ans = await _record(repo, emp, sv, st, i, q, value=value, mood=_mood_for(chosen))
        await _option_flags(repo, emp, sv, q, chosen, ans)
        lead = next((o for o in chosen if o.reply or o.actions or o.ask), None)
        if lead is None:
            await _show(cb, emp, st, *_advance(emp, sv, st))
        else:
            await _after_choice(cb, emp, sv, st, i, lead, value)
    elif act in ("n", "sk"):
        await _show(cb, emp, st, *_advance(emp, sv, st))
    elif act in ("rc", "hc"):
        if _contacted(st, i, act):
            await cb.toast(T.ALREADY_SENT)
            return
        await _ticket(ctx, repo, emp, _contact_text(emp, sv, q, st.get("value")), category="contact_request")
        ack = tv(emp, T.CONTACT_RECRUITER if act == "rc" else T.CONTACT_HR)
        await _show(cb, emp, st, *_advance(emp, sv, st, prefix=_ack_prefix(emp, sv, st, i, ack)))
    elif act in ("fw", "fh"):
        _set_wait(emp, st, act)
        current = (st.get("view") or {}).get("text") or cb.orig_text
        ask = tv(emp, T.FORWARD_ASK if act == "fw" else T.FORWARD_HR_ASK)
        await _show(cb, emp, st, _join(current, ask), keyboard([_btn(T.BTN_SKIP, sv.code, i, "sk")]))
    elif act == "acc":
        if _contacted(st, i, act):
            await cb.toast(T.ALREADY_SENT)
            return
        ack = await _remind_access(ctx, repo, emp)
        await _show(cb, emp, st, *_advance(emp, sv, st, prefix=_ack_prefix(emp, sv, st, i, ack)))
    else:
        await cb.toast(T.STALE)


def _contacted(st: dict | None, i: int, act: str) -> bool:
    """Обращение «связаться» / напоминание про доступы по этому вопросу уже было. Иначе — отмечаем."""
    if st is None:
        return False
    key = f"{i}:{act}"
    done = list(st.get("contacted") or [])
    if key in done:
        return True
    st["contacted"] = [*done, key]
    return False


def _ack_prefix(emp, sv: C.Survey, st: dict, i: int, ack: str) -> str:
    """На последнем вопросе без завершающей фразы оставляем ответ Корсика, чтобы сообщение не потеряло смысл."""
    if _is_last(emp, sv, i) and sv.outro == "":
        return _join((st.get("view") or {}).get("text"), ack)
    return ack


def _contact_text(emp, sv: C.Survey, q: C.Question, value: str | None) -> str:
    return T.TICKET_CONTACT.format(survey=sv.title, question=_name(q.text, emp), answer=value or "—")


async def _after_choice(cb, emp, sv: C.Survey, st: dict, i: int, opt: C.Option, mark: str) -> None:
    inter = _interjection(emp, sv, st, i, opt, mark)
    if inter is None:
        await _show(cb, emp, st, *_advance(emp, sv, st, prefix=_name(opt.reply, emp)))
        return
    text, kb, wait = inter
    _set_wait(emp, st, wait)
    await _show(cb, emp, st, text, kb)


async def _stale_action(cb, emp, sv: C.Survey, i: int, act: str) -> None:
    """Кнопка из сообщения, которое уже не текущее. Действия «связаться» и «доступы» работают и так.

    Повторное нажатие в живом сообщении опроса (двойной тап, пока шёл первый ответ) — только всплывашка:
    второе обращение не нужно, а правка затёрла бы следующий вопрос.
    """
    st = _state(emp)
    live = bool(st and cb.mid and cb.mid == st.get("mid"))
    if act in ("acc", "rc", "hc"):
        same = st if st and st.get("code") == sv.code else None
        if live or _contacted(same, i, act):
            await cb.toast(T.ALREADY_SENT)
            return
        if same is not None:
            _save(emp, same)
    if act == "acc":
        ack = await _remind_access(cb.ctx, cb.repo, emp)
        await cb.replace(_join(cb.orig_text, ack), [], employee=emp)
        return
    if act in ("rc", "hc"):
        qs = sv.visible(_site(emp))
        q = qs[i] if i < len(qs) else qs[-1]
        await _ticket(cb.ctx, cb.repo, emp, _contact_text(emp, sv, q, None), category="contact_request")
        ack = tv(emp, T.CONTACT_RECRUITER if act == "rc" else T.CONTACT_HR)
        await cb.replace(_join(cb.orig_text, ack), [], employee=emp)
        return
    done = (_history(emp).get(sv.code) or {}).get("done_at")
    await cb.toast(T.DONE_ALREADY if done else T.STALE)


async def _continue(cb, emp, sv: C.Survey, st: dict | None) -> None:
    """«Продолжить опрос» из напоминания: опрос переезжает в это сообщение."""
    if not st or st.get("code") != sv.code or st.get("done"):
        await cb.replace(T.DONE_ALREADY, [], employee=emp)
        return
    view = st.get("view") or {}
    if not view.get("text"):
        view = dict(zip(("text", "kb"), question_view(emp, sv, st["q"]), strict=True))
    old = st.get("mid")
    if old and old != cb.mid:
        await runtime.edit(old, T.MOVED_DOWN, [], chat_id=getattr(emp, "chat_id", None))
    st["mid"] = cb.mid
    # опрос ждал текст («Что мешает?», свой вариант): ждём заново, иначе ответ уйдёт в базу знаний
    mode = st.get("wait") or view.get("wait")
    if mode:
        _set_wait(emp, st, mode)
    await _show(cb, emp, st, view["text"], view.get("kb") or [])


# --- текст ---------------------------------------------------------------------------

def _parse_state(state: str | None) -> tuple[str, int, str] | None:
    parts = (state or "").split(":")
    if len(parts) != 4 or parts[0] != PREFIX:
        return None
    try:
        return parts[1], int(parts[2]), parts[3]
    except ValueError:
        return None


async def handle_text(ctx, repo, emp, text: str, msg: dict) -> bool | None:
    """Текст новичка, пока опрос ждёт свой вариант, причину или открытый ответ.

    False — опрос этот текст уже не ждёт: бот ведёт его обычным маршрутом (красный флаг, окно ответа HR,
    база знаний).
    """
    st = _state(emp)
    parsed = _parse_state(emp.dialog_state)
    sv = C.get(parsed[0]) if parsed else None
    fresh = False
    if st and st.get("wait_at"):
        try:
            fresh = datetime.now(UTC) - datetime.fromisoformat(st["wait_at"]) < TEXT_WAIT_TTL
        except ValueError:
            fresh = False
    if not (sv and st and st.get("code") == sv.code and st.get("q") == parsed[1] and st.get("wait") and fresh):
        # опрос давно брошен или состояние не совпало: это обычный текст в бот. Режим ожидания остаётся
        # в st["view"]: «Продолжить опрос» из напоминания вернёт его.
        if st and st.get("wait"):
            _set_wait(emp, st, None)
            _save(emp, st)
        elif (emp.dialog_state or "").split(":", 1)[0] == PREFIX:
            emp.dialog_state = None
        return False
    text = (text or "").strip()
    if not text:
        await ctx.sender.send(repo, tv(emp, T.TEXT_ONLY), chat_id=emp.chat_id, user_id=emp.max_user_id,
                              employee=emp)
        return

    from app.signals import service as signals

    mode = st["wait"]
    qs = sv.visible(_site(emp))
    i = st["q"]
    q = qs[min(i, len(qs) - 1)]
    ans = await _attach_text(repo, emp, sv, st, q, text)
    flags = await signals.check_text(repo, emp, text, source="survey", answer=ans)
    notice = await signals.employee_notice(repo, emp, flags)
    finishing = i >= len(qs) - 1

    ack: str | None
    if mode in ("fw", "fh", "problem"):
        if not any(f.level == "red" for f in flags):  # красный флаг уже создал обращение с этим текстом
            body = T.TICKET_PROBLEM.format(text=text, survey=sv.title, question=_name(q.text, emp),
                                           answer=st.get("value") or "—")
            await _ticket(ctx, repo, emp, body, category=q.topic)
        ack = tv(emp, T.FORWARDED_HR if mode == "fh" else T.FORWARDED)
    else:
        # на последнем вопросе «записал» не нужно: следом и так «спасибо»
        ack = None if finishing and sv.outro != "" else tv(emp, T.NOTED)
    if notice:
        ack = notice

    # старое сообщение остаётся с отметкой выбора, без кнопок
    old_mid = st.get("mid")
    old_text = (st.get("view") or {}).get("text")
    if old_mid and old_text:
        await ctx.sender.edit(old_mid, old_text, attachments=_atts(st, old_mid, []), key=emp.chat_id)

    # ответ Корсика из ТЗ остался выше в старом сообщении, здесь только подтверждение и следующий вопрос
    text_out, kb = _advance(emp, sv, st, prefix=ack)
    mids = await ctx.sender.send(repo, text_out, chat_id=emp.chat_id, user_id=emp.max_user_id,
                                 attachments=kb or None, employee=emp)
    st["mid"] = mids[-1] if mids else None
    st["view"] = {"text": text_out, "kb": kb, "wait": st.get("wait")}
    history = {"done_at": st.pop("_done_at")} if st.get("_done_at") else {}
    _save(emp, st, **history)


# --- напоминания -----------------------------------------------------------------------

def reminder_due(emp, st: dict, tz: str, now: datetime | None = None) -> int:
    """Какое напоминание пора слать: 0 — никакое, 1 — на следующий рабочий день, 2 — через рабочий день.

    Дни — по производственному календарю, как шаги сценария: в выходные и праздники не пишем.
    """
    if not st or st.get("done") or not st.get("started_at"):
        return 0
    try:
        started = datetime.fromisoformat(st["started_at"])
    except ValueError:
        return 0
    zone = ZoneInfo(tz or settings.tz_default)
    local_now = emp_now(emp, now).astimezone(zone)
    today, start = local_now.date(), started.astimezone(zone).date()
    if not start < today <= start + timedelta(days=31) or local_now.hour < REMIND_HOUR or not is_workday(today):
        return 0
    days = sum(1 for k in range(1, (today - start).days + 1) if is_workday(start + timedelta(days=k)))
    if days < 1 or days >= STALE_DAYS:
        return 0
    due = min(days, 2)
    return due if due > int(st.get("reminded") or 0) else 0


async def send_reminder(repo, emp, st: dict, due: int) -> list[str] | None:
    """Напоминание с кнопкой «Продолжить опрос». st — свежее состояние опроса (после lock_employee)."""
    text = tv(emp, T.REMINDER).format(name=getattr(emp, "first_name", "") or "")
    kb = keyboard([callback(T.BTN_CONTINUE, f"{PREFIX}:{st['code']}:go")])
    mids = await runtime.send(repo, text, employee=emp, attachments=kb)
    st["reminded"] = due
    _save(emp, st, reminded=due)
    log.info("Напоминание %s об опросе %s новичку %s", due, st["code"], emp.id)
    return mids


async def remind_all(session, now: datetime | None = None, employee_ids=None) -> int:
    """Задача survey_reminders: разослать наступившие напоминания. Возвращает число отправленных.

    employee_ids — только эти новички (тесты, точечный запуск); None — все активные.
    """
    from sqlalchemy import select

    from app.bot.repo import SqlRepo
    from app.db.models import Employee, Site

    repo = SqlRepo(session)
    tzs = {s.code: s.tz for s in (await session.scalars(select(Site))).all()}
    emps = (await session.scalars(select(Employee).where(
        Employee.status == "active", Employee.dialog_data.has_key(STATE_KEY),
        *([Employee.id.in_(list(employee_ids))] if employee_ids is not None else [])))).all()
    sent = 0
    for emp in emps:
        st = _state(emp)
        if not st:
            continue
        sv = C.get(st.get("code", ""))
        if sv is None or not sv.remind:
            continue
        if not (emp.chat_id or emp.max_user_id):
            continue
        tz = tzs.get(emp.site_code) or settings.tz_default
        if not reminder_due(emp, st, tz, now):
            continue
        # бот мог поменять опрос, пока мы шли по списку: перечитываем под блокировкой и проверяем заново
        await lock_employee(session, emp)
        st = _state(emp)
        due = reminder_due(emp, st, tz, now) if st else 0
        if due:
            try:
                await send_reminder(repo, emp, st, due)
                sent += 1
            except Exception:
                log.exception("Напоминание об опросе новичку %s", emp.id)
    return sent
