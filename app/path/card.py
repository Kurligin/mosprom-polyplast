"""Карточка «Пути новичка» картинкой: 7 точек змейкой, «вы здесь», следующая веха.

Стиль Полипластика: синие #005593 / #226AAF, тёмно-синий #00173E, голубая заливка #E9F1FA, скобки [ ] у плашек,
шрифт Exo 2. HTML (Jinja2) → PNG в общем Chromium сводки (app/reports/render.py). Нет Chromium, ошибка
отрисовки или загрузки — None: сообщение уходит без картинки, бот не падает.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.path import texts as T
from app.path.service import AHEAD, DONE, SKIPPED, PathState

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1000
FILE_NAME = "korsik-path.png"
RENDER_TIMEOUT = 10.0   # холодный старт Chromium; новичок к этому времени уже видит «спасибо»
EDIT_PAUSES = (1.0, 2.0, 3.0)
TEMPLATES = Path(__file__).resolve().parent / "templates"
FONT = Path(__file__).resolve().parents[2] / "media" / "demo" / "assets" / "fonts" / "Exo2.ttf"

# Змейка: первый ряд слева направо, второй — справа налево, поворот справа
COLS = (190, 423, 656, 889)
ROWS = (420, 680)
PER_ROW = 4

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                   trim_blocks=True, lstrip_blocks=True)


@lru_cache(maxsize=1)
def _font_data() -> str | None:
    """Exo 2 внутрь HTML (data: URL): страница открыта из about:blank, file:// ей недоступен."""
    try:
        return base64.b64encode(FONT.read_bytes()).decode("ascii")
    except OSError:
        log.warning("Шрифт %s не найден, карточка пути со шрифтом по умолчанию", FONT)
        return None


def _points(n: int) -> list[tuple[int, int]]:
    pts = []
    for i in range(n):
        row, k = divmod(i, PER_ROW)
        col = k if row % 2 == 0 else PER_ROW - 1 - k
        pts.append((COLS[col], ROWS[min(row, len(ROWS) - 1)]))
    return pts


def _segments(state: PathState, pts: list[tuple[int, int]]) -> list[dict]:
    """Отрезки между точками: пройденный путь сплошной, впереди — пунктир."""
    out = []
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        if y1 == y2:
            d = f"M{x1} {y1} L{x2} {y2}"
        else:  # поворот змейки справа
            bulge = x1 + 110
            d = f"M{x1} {y1} C{bulge} {y1} {bulge} {y2} {x2} {y2}"
        out.append({"d": d, "walked": state.milestones[i + 1].status != AHEAD})
    return out


def card_context(state: PathState, name: str | None) -> dict:
    pts = _points(state.total)
    nxt = state.next_milestone
    points = []
    for i, (m, (x, y)) in enumerate(zip(state.milestones, pts, strict=True)):
        points.append({"x": x, "y": y, "n": i + 1, "label": m.label, "status": m.status,
                       "here": i == state.current, "next": i == state.next})
    return {
        "width": WIDTH, "height": HEIGHT, "font": _font_data(),
        "eyebrow": T.CARD_EYEBROW,
        "title": T.CARD_TITLE.format(n=state.passed, total=state.total),
        "sub": T.CARD_SUB.format(name=name) if name else T.CARD_SUB.split(" · ", 1)[1],
        "points": points, "segments": _segments(state, pts),
        "here": T.CARD_HERE, "skipped": T.CARD_SKIPPED,
        "footer": T.CARD_NEXT.format(next=nxt.label) if nxt else (T.CARD_DONE if state.finished else None),
        "legend": T.CARD_LEGEND, "DONE": DONE, "SKIPPED": SKIPPED, "AHEAD": AHEAD,
    }


def render_html(state: PathState, name: str | None = None) -> str:
    return _env.get_template("path.html").render(**card_context(state, name))


async def render_png(state: PathState, name: str | None = None) -> bytes:
    from app.reports import render

    return await render.screenshot(render_html(state, name), WIDTH, HEIGHT)


async def attachment(repo, sender, state: PathState, emp) -> dict | None:
    """Карточка пути как вложение «МАКС» или None (нет бота, нет Chromium, ошибка — без картинки)."""
    if sender is None:
        return None
    try:
        png = await asyncio.wait_for(render_png(state, getattr(emp, "first_name", None)), RENDER_TIMEOUT)
        from app.bot import uploads

        return await uploads.attachment_bytes(png, FILE_NAME, "image", repo=repo, sender=sender)
    except Exception as e:  # noqa: BLE001 — без картинки сообщение всё равно уходит
        log.warning("Карточка пути новичка %s не отрисована: %s", getattr(emp, "id", None), e)
        return None


async def attach(repo, sender, state: PathState, emp, *, mid: str, text: str, atts: list[dict],
                 chat_id: int | None = None) -> dict | None:
    """Дописать карточку в уже показанное сообщение `mid` (PUT /messages: тот же текст, карточка вместо
    картинки шага, кнопки как были). Свежезагруженная картинка у «МАКС» бывает «ещё не готова»
    (`attachment.not.ready`) — повторяем с паузой, потом сдаёмся: сообщение остаётся со строкой, без картинки.
    """
    att = await attachment(repo, sender, state, emp)
    if att is None:
        return None
    from app.bot import uploads

    new_atts = [att, *(a for a in atts or [] if a.get("type") != "image")]
    pauses = list(EDIT_PAUSES)
    sleep = getattr(sender, "_sleep", asyncio.sleep)
    while True:
        try:
            await sender.call(chat_id, sender.api.edit_message, mid, text, attachments=new_atts)
            return att
        except Exception as e:  # noqa: BLE001
            if uploads.is_not_ready(e) and pauses:
                await sleep(pauses.pop(0))
                continue
            log.warning("Карточка пути новичка %s не встала в сообщение %s: %s", getattr(emp, "id", None), mid, e)
            return None
