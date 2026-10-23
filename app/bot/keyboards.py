"""Inline-клавиатуры «МАКС»: кнопки callback / link / open_app."""

from __future__ import annotations


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def link(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def open_app(text: str, web_app: str, payload: str | None = None) -> dict:
    # web_app — публичное имя бота, к которому привязан Mini App
    btn = {"type": "open_app", "text": text, "web_app": web_app}
    if payload:
        btn["payload"] = payload
    return btn


def keyboard(*rows: list[dict]) -> list[dict]:
    """Готовый список attachments с одной inline-клавиатурой."""
    return [{"type": "inline_keyboard", "payload": {"buttons": [list(r) for r in rows]}}]
