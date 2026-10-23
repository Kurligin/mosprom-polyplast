"""Подключение базы знаний к боту: свободный вопрос новичка и кнопка «Связаться с HR» (kb:)."""

from __future__ import annotations

import sys

from app.bot import hooks
from app.kb import service
from app.kb.embed import warm_up_in_background

hooks.on_free_text(service.answer_question)
hooks.on_callback("kb")(service.on_button)

# Модель эмбеддингов грузим заранее, чтобы первый вопрос не ждал её загрузки
if "pytest" not in sys.modules:
    warm_up_in_background()
