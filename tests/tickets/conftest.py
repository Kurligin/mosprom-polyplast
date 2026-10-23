"""Те же фикстуры, что у API: БД в транзакции, синтетические люди, фейковый бот."""

from tests.api.conftest import _settings, bot, client, session, world  # noqa: F401
from tests.scenario.conftest import db  # noqa: F401  (отдельная БД с настоящими коммитами: гонки)
