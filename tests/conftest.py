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
