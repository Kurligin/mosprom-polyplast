"""Фикстуры проверок безопасности: те же, что у API (реальная БД в транзакции с откатом, синтетика)."""

from tests.api.conftest import _settings, bot, client, session, world  # noqa: F401
