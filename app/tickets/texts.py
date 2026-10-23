"""Тексты обращений: сообщения новичку и уведомления в HR-группе.

Коротко и по-человечески, без длинных тире и канцелярита. Пары (на ты, на вы) — через app.bot.texts.tv().
"""

from __future__ import annotations

# --- новичку ---

# После первого ответа HR: подсказка, что можно ответить прямо в чате
REPLY_HINT = (
    "Если хочешь ответить, просто напиши сюда, я передам.",
    "Если хотите ответить, просто напишите сюда, я передам.",
)
# Первое сообщение новичка после ответа HR
RELAYED = ("Передал HR.", "Передал HR.")

# --- HR-группа ---

RED_HEAD = "🔴 Красный флаг, обращение #{id}"
HEAD = "Новое обращение #{id}"
WHO = "{name}, {site}, {day}"
# Точку в конце ставит код: сокращённое имя «Фамилия И.» уже с точкой
RECRUITER = "Рекрутер: {name}"
NO_RECRUITER = "Рекрутер не назначен"
STATUS = {
    "new": "Ждёт ответа.",
    "in_progress": "В работе: {name}",
    "closed": "Закрыто: {name}",
}
FOLLOW_UP = "Новый ответ в обращении #{id}\n{name}, {site}\n\n«{text}»"
ATTACHMENT = "[вложение]"

BTN_APP = "Открыть в Mini App"
BTN_TAKE = "Взять себе"

# --- кнопка «Взять себе» в группе ---

TAKE_ONLY_STAFF = "Кнопка для HR"
TAKE_NOT_FOUND = "Обращение не найдено"
TAKE_MANAGER = "Обращения разбирает HR"
TAKE_CLOSED = "Обращение уже закрыто"

# --- категории ---

CATEGORY_LABELS = {
    "safety": "Охрана труда",
    "harassment": "Травля и давление",
    "intent_to_leave": "Намерение уйти",
    "health": "Здоровье",
    "unknown": "Бот не нашёл ответ",
    "contact_request": "Просьба связаться",
    "question": "Вопрос HR",
}
PERSONAL_LABEL = "Личный вопрос"
FLAG_LABEL = "Красный флаг"


def sentence(text: str) -> str:
    """Точка в конце, если её ещё нет."""
    return text if text.endswith((".", "!", "?")) else text + "."


def day_label(day: int) -> str:
    if day >= 1:
        return f"день {day}"
    if day == 0:
        return "выходит завтра"
    return f"до выхода {1 - day} дн."
