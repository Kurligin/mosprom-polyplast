"""«Путь новичка»: 7 вех адаптации. Знакомство (засчитано сразу, фора на старте) и 6 опросов по порядку.

- Веха-опрос пройдена, когда новичок завершил опрос (`dialog_data["surveys"][код]["done_at"]`), ответы любые.
- Опрос не завершён, а путь ушёл дальше (опрос закрыт новым, начат или пройден следующий) — веха пропущена.
  Пропуск показываем спокойно, без укоров, и в «пройдено N из 7» не считаем.
- Всё считается по фактическим опросам в dialog_data: машина времени демо ничего не ломает.

Где показывается: строка о вехе — сразу в том же сообщении, где веха пройдена («спасибо» опроса, финал
знакомства); карточка пути дописывается в него же правкой, когда картинка загружена. Строка «Пройдено N из 7
вех» — в приветствии на /start. Новых сообщений новичку нет. Вех 7, а если на площадке нет опроса первого
дня — 6. Последняя веха — один раз сообщение в HR-группу про мерч (`dialog_data["path"]["hr_finished_at"]`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import settings
from app.path import texts as T

log = logging.getLogger(__name__)

INTRO = "intro"
DATA_KEY = "path"
DONE, SKIPPED, AHEAD = "done", "skipped", "ahead"


@dataclass(frozen=True)
class Milestone:
    code: str
    label: str      # короткое название для карточки и строки «Дальше: …»
    title: str      # название опроса из config/surveys.yaml
    status: str     # done | skipped | ahead


@dataclass(frozen=True)
class PathState:
    milestones: tuple[Milestone, ...]
    passed: int               # пройдено вех (пропущенные не считаются)
    current: int | None       # «вы здесь»: последняя веха позади (пройдена или пропущена)
    next: int | None          # следующая веха впереди

    @property
    def total(self) -> int:
        return len(self.milestones)

    @property
    def finished(self) -> bool:
        """Последняя веха (итоговый опрос) пройдена."""
        return bool(self.milestones) and self.milestones[-1].status == DONE

    @property
    def next_milestone(self) -> Milestone | None:
        return self.milestones[self.next] if self.next is not None else None

    def index(self, code: str) -> int | None:
        return next((i for i, m in enumerate(self.milestones) if m.code == code), None)


def _surveys(site: str | None) -> list:
    """Опросы по порядку из config/surveys.yaml, у которых есть вопросы для площадки новичка."""
    from app.surveys import config as C

    return [sv for sv in C.load().values() if sv.visible(site)]


def _onboarded(emp) -> bool:
    data = getattr(emp, "dialog_data", None) or {}
    return bool(data.get("onboarded")) or getattr(emp, "status", None) in ("active", "finished")


def path_state(emp) -> PathState:
    """Состояние пути новичка по фактическим опросам в dialog_data."""
    history = (getattr(emp, "dialog_data", None) or {}).get("surveys") or {}
    history = history if isinstance(history, dict) else {}
    surveys = _surveys(getattr(emp, "site_code", None))
    codes = [sv.code for sv in surveys]
    # опрос с этого индекса и дальше уже начинался: всё, что раньше и не пройдено, — позади
    reached = [bool((history.get(c) or {}).get("started_at") or (history.get(c) or {}).get("done_at"))
               for c in codes]

    items = [Milestone(INTRO, T.LABELS[INTRO], T.INTRO_TITLE, DONE if _onboarded(emp) else AHEAD)]
    for i, sv in enumerate(surveys):
        h = history.get(sv.code) or {}
        if h.get("done_at"):
            status = DONE
        elif h.get("closed_at") or any(reached[i + 1:]):
            status = SKIPPED
        else:
            status = AHEAD
        items.append(Milestone(sv.code, T.LABELS.get(sv.code, sv.title), sv.title, status))

    behind = [i for i, m in enumerate(items) if m.status != AHEAD]
    current = max(behind) if behind else None
    start = 0 if current is None else current + 1
    nxt = next((i for i in range(start, len(items)) if items[i].status == AHEAD), None)
    return PathState(tuple(items), sum(m.status == DONE for m in items), current, nxt)


def eligible(emp) -> bool:
    """Путь показываем живому подключившемуся новичку после знакомства. Синтетике и остановившим — ничего."""
    return (settings.path_enabled and not getattr(emp, "is_synthetic", False)
            and getattr(emp, "status", None) != "stopped" and _onboarded(emp))


def milestone_line(state: PathState, code: str) -> str | None:
    """«Веха 3 из 7 пройдена. Дальше: «Две недели».» Одна и та же при любых ответах."""
    i = state.index(code)
    if i is None or state.milestones[i].status != DONE:
        return None
    n, total = i + 1, state.total
    if i == total - 1:
        return T.LINE_LAST.format(n=n, total=total)
    nxt = state.next_milestone
    if nxt is None:
        return T.LINE_NO_NEXT.format(n=n, total=total)
    return T.LINE.format(n=n, total=total, next=nxt.label)


def hello_line(state: PathState) -> str:
    return T.HELLO.format(n=state.passed, total=state.total)


# --- веха пройдена: строка, карточка, HR-группа ----------------------------------------------------

def line_for(emp, code: str) -> str | None:
    """Строка о только что пройденной вехе для того же сообщения или None (выключено, не положено, ошибка).

    Считается сразу, без картинки: сообщение «спасибо» уходит без задержки.
    """
    if not eligible(emp):
        return None
    try:
        return milestone_line(path_state(emp), code)
    except Exception:  # путь новичка не ломает опрос и знакомство
        log.exception("Путь новичка %s: состояние", getattr(emp, "id", None))
        return None


async def after_shown(repo, emp, code: str, *, sender, mid: str | None, text: str, atts: list[dict],
                      chat_id: int | None = None, with_card: bool = True) -> dict | None:
    """Новичок уже видит «спасибо» со строкой о вехе (сообщение `mid`). Теперь:

    1. карточка пути: отрисовка, загрузка и правка того же сообщения (`card.attach`, повтор на not.ready);
    2. последняя веха — один раз сообщение в HR-группу про мерч, уже после того, как новичок увидел «спасибо».

    Возвращает вложение карточки, если оно встало в сообщение, иначе None. Ошибки не выходят наружу.
    """
    if not eligible(emp):
        return None
    try:
        state = path_state(emp)
    except Exception:
        log.exception("Путь новичка %s: состояние", getattr(emp, "id", None))
        return None
    att = None
    if with_card and mid and sender is not None:
        from app.path import card

        att = await card.attach(repo, sender, state, emp, mid=mid, text=text, atts=atts, chat_id=chat_id)
    if state.finished and code == state.milestones[-1].code:
        try:
            await notify_finished(repo, emp, sender=sender)
        except Exception:
            log.exception("Путь новичка %s: уведомление HR-группы", getattr(emp, "id", None))
    return att


def _data(emp) -> dict:
    d = (getattr(emp, "dialog_data", None) or {}).get(DATA_KEY)
    return dict(d) if isinstance(d, dict) else {}


async def notify_finished(repo, emp, *, sender) -> bool:
    """Один раз: «путь пройден, можно вручить мерч» в HR-группу. Без ответов опросов и обращений."""
    if not eligible(emp) or _data(emp).get("hr_finished_at") or sender is None:
        return False
    chat_id = await repo.get_setting("hr_group_chat_id")
    if not chat_id:
        log.info("Путь новичка %s пройден, HR-группа не подключена", emp.id)
        return False
    from app.chats.service import display_name, site_name

    text = T.HR_FINISHED.format(name=display_name(emp), site=await site_name(repo.s, emp))
    mids = await sender.send(repo, text, chat_id=int(chat_id))
    if not mids:
        return False
    data = dict(emp.dialog_data or {})
    data[DATA_KEY] = {**_data(emp), "hr_finished_at": datetime.now(UTC).isoformat()}
    emp.dialog_data = data
    log.info("Путь новичка %s пройден: HR-группа знает", emp.id)
    return True
