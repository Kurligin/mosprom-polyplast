"""Подключение модулей к боту и планировщику: импорт app/<модуль>/hooks.py регистрирует обработчики.

Модуля ещё нет — пропускаем молча. Ошибка внутри модуля — в лог, остальные модули работают.
"""

from __future__ import annotations

import importlib
import logging

log = logging.getLogger(__name__)

MODULES = [
    # Порядок важен: pre_route перебираются по порядку загрузки.
    # Сигналы раньше обращений: текст с красным флагом не должен уйти «ответом» в старое обращение.
    "app.signals.hooks",    # флаги из ответов и свободного текста
    "app.tickets.hooks",    # обращения: фабрика тикетов, ответ в открытое обращение
    "app.surveys.hooks",    # опросы: кнопки sv:, состояния sv:
    "app.scenario.hooks",   # сценарий: тик, кнопки sc:, /food, после знакомства
    "app.kb.hooks",         # база знаний: свободный вопрос, кнопки kb:
    "app.manager.hooks",    # руководитель: push «проведи встречу», кнопки mg:
    "app.reports.hooks",    # еженедельная сводка
]

_loaded = False


def load_plugins() -> list[str]:
    global _loaded
    loaded: list[str] = []
    for name in MODULES:
        try:
            importlib.import_module(name)
            loaded.append(name)
        except ModuleNotFoundError as e:
            if e.name and name.startswith(e.name):
                continue  # модуля ещё нет
            log.exception("Модуль %s не загружен", name)
        except Exception:
            log.exception("Модуль %s не загружен", name)
    _loaded = True
    return loaded
