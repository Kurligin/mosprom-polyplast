"""Доступ к БД для обработчиков бота. В тестах подменяется фейком с тем же интерфейсом."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Consent, Employee, Message, Setting, Staff, Ticket


class SqlRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    # --- поиск ---

    async def employee_by_invite(self, code: str):
        return (await self.s.execute(select(Employee).where(Employee.invite_code == code))).scalar_one_or_none()

    async def staff_by_bind(self, code: str):
        return (await self.s.execute(select(Staff).where(Staff.bind_code == code))).scalar_one_or_none()

    async def employee_by_user(self, max_user_id: int):
        """Новичок по аккаунту «МАКС». Строка блокируется до конца события (FOR NO KEY UPDATE):
        планировщик и API, которые меняют dialog_data, дождутся коммита бота и перечитают свежие данные."""
        return (await self.s.execute(
            select(Employee).where(Employee.max_user_id == max_user_id)
            .with_for_update(key_share=True))).scalar_one_or_none()

    async def staff_by_user(self, max_user_id: int):
        return (await self.s.execute(select(Staff).where(Staff.max_user_id == max_user_id))).scalar_one_or_none()

    # --- запись ---

    async def add_consent(self, employee_id: int, purpose: str, given: bool, text_version: str = "v1"):
        self.s.add(Consent(employee_id=employee_id, purpose=purpose, given=given,
                           given_at=datetime.now(UTC), text_version=text_version))
        await self.s.flush()

    async def add_message(self, *, employee_id, staff_id, chat_id, direction, text, mid=None, ticket_id=None):
        self.s.add(Message(employee_id=employee_id, staff_id=staff_id, chat_id=chat_id, direction=direction,
                           text=text or "", mid=mid, ticket_id=ticket_id))
        await self.s.flush()

    async def create_ticket(self, *, employee_id: int, source: str, text: str, assignee_id=None,
                            category=None, is_personal: bool = False):
        t = Ticket(employee_id=employee_id, source=source, text=text, status="new",
                   assignee_id=assignee_id, category=category, is_personal=is_personal)
        self.s.add(t)
        await self.s.flush()
        return t

    async def get_setting(self, key: str):
        row = await self.s.get(Setting, key)
        return row.value if row else None

    async def set_setting(self, key: str, value) -> None:
        row = await self.s.get(Setting, key)
        if row:
            row.value = value
        else:
            self.s.add(Setting(key=key, value=value))
        await self.s.flush()

    async def flush(self) -> None:
        await self.s.flush()


async def lock_employee(session, emp) -> None:
    """Перечитать новичка из БД с блокировкой строки до конца транзакции (FOR NO KEY UPDATE).

    Зовём перед тем, как менять dialog_data вне обработчика бота (планировщик, API): бот, задача и API
    пишут JSONB целиком, и без этого более поздний коммит затирает чужие ключи. Свои изменения новичка
    сначала уходят в БД (flush), потом объект обновляется: меняем dialog_data поверх свежих данных.
    """
    if session is None or not isinstance(emp, Employee) or emp.id is None:
        return
    await session.flush()
    await session.execute(select(Employee).where(Employee.id == emp.id).with_for_update(key_share=True)
                          .execution_options(populate_existing=True))
