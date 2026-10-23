"""Картинка сводки: Jinja2-шаблон → PNG 1080 × 1350 через Playwright.

Chromium запускается один раз на процесс и переиспользуется; на каждую картинку — новая вкладка.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1350
TEMPLATES = Path(__file__).resolve().parent / "templates"

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                   trim_blocks=True, lstrip_blocks=True)
_pw = None
_browser = None
_lock = asyncio.Lock()


def _num1(x) -> str:
    return "—" if x is None else f"{x:.1f}".replace(".", ",")


def _pct(x) -> str:
    """Доля настроения; None — мало ответов с настроением, цифры нет."""
    return "—" if x is None else f"{round(x * 100)}%"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


_env.filters.update(num1=_num1, pct=_pct, plural=_plural)


def render_html(report: dict) -> str:
    return _env.get_template("weekly.html").render(r=report, width=WIDTH, height=HEIGHT)


async def _get_browser():
    global _pw, _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            from playwright.async_api import async_playwright

            if _pw is None:
                _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch()
            log.info("Chromium для сводки запущен")
    return _browser


async def render_png(report: dict) -> bytes:
    browser = await _get_browser()
    page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
    try:
        await page.set_content(render_html(report), wait_until="load")
        await page.evaluate("document.fonts.ready")
        return await page.screenshot(type="png", clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
    finally:
        await page.close()


async def close() -> None:
    """Закрыть браузер (тесты, остановка процесса)."""
    global _pw, _browser
    async with _lock:
        if _browser is not None:
            await _browser.close()
            _browser = None
        if _pw is not None:
            await _pw.stop()
            _pw = None
