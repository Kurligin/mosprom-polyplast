"""Общее для всех тестов.

Документы заказчика (docs/*.md) — внутренние и в публичную версию репозитория не попадают.
Тесты, которым нужен их текст, помечены `needs_customer_docs` и без них пропускаются, а не падают.
"""

from pathlib import Path

import pytest

CUSTOMER_DOCS = Path(__file__).resolve().parents[1] / "docs"


def customer_docs_available() -> bool:
    return (CUSTOMER_DOCS / "pvtr.md").exists()


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_customer_docs: нужен текст документов заказчика из docs/")


def pytest_collection_modifyitems(config, items):
    if customer_docs_available():
        return
    skip = pytest.mark.skip(reason="нет документов заказчика в docs/ (публичная версия репозитория)")
    for item in items:
        if "needs_customer_docs" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def path_off(monkeypatch):
    """«Путь новичка» (app/path) дописывает строку о вехе и карточку к «спасибо» опроса, финалу знакомства
    и приветствию. Тесты других модулей сверяют эти сообщения дословно, поэтому здесь модуль выключен.
    Тесты пути (tests/path, фикстура `path_on`) и сквозной маршрут (tests/e2e) включают его сами."""
    from app.config import settings

    monkeypatch.setattr(settings, "path_enabled", False)


@pytest.fixture
def path_on(path_off, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "path_enabled", True)
