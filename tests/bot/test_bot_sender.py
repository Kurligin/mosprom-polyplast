"""Очередь отправки: нарезка, 429, 403, лимиты."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.bot.api import MaxApiError
from app.bot.sender import RateLimiter, Sender, split_text


async def _no_sleep(_):
    return None


def test_split_text():
    text = ("абзац " * 300 + "\n\n") * 5
    parts = split_text(text, 3500)
    assert len(parts) > 1
    assert all(len(p) <= 3500 for p in parts)
    assert split_text("коротко") == ["коротко"]


@pytest.mark.asyncio
async def test_long_text_split_keyboard_on_last(api, repo):
    s = Sender(api, sleep=_no_sleep)
    kb = [{"type": "inline_keyboard", "payload": {"buttons": [[{"type": "callback", "text": "ок", "payload": "x"}]]}}]
    await s.send(repo, "слово " * 1500, chat_id=1, attachments=kb)
    assert len(api.sent) >= 2
    assert api.sent[0]["attachments"] is None and api.sent[-1]["attachments"] == kb
    assert len(repo.messages) == len(api.sent)


@pytest.mark.asyncio
async def test_429_retry(api, repo):
    s = Sender(api, sleep=_no_sleep)
    api.fail_queue = [MaxApiError(429, "too.many.requests"), MaxApiError(429)]
    mids = await s.send(repo, "привет", chat_id=1)
    assert mids and len(api.sent) == 1


@pytest.mark.asyncio
async def test_403_marks_stopped(api, repo):
    s = Sender(api, sleep=_no_sleep)
    emp = SimpleNamespace(id=1, status="active", chat_id=1, max_user_id=2)
    api.fail_queue = [MaxApiError(403, "chat.denied")]
    assert await s.send(repo, "привет", employee=emp) is None
    assert emp.status == "stopped"
    assert api.sent == []


@pytest.mark.asyncio
async def test_rate_limiter_per_chat():
    lim = RateLimiter(2, per=0.2)
    t0 = time.monotonic()
    for _ in range(3):
        await lim.acquire("chat")
    assert time.monotonic() - t0 >= 0.15
    # другой чат не ждёт
    t1 = time.monotonic()
    await asyncio.gather(lim.acquire("other"), lim.acquire("other2"))
    assert time.monotonic() - t1 < 0.1
