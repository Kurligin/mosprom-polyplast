"""Фейковый клиент API «МАКС» и фейковый репозиторий для тестов бота."""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from app.bot.api import MaxApiError
from app.bot.handlers import BotCtx
from app.bot.sender import Sender
from tests.surveys.fakes import session, world  # noqa: F401  (настоящая БД: test_bot_dialogs.py)


class FakeApi:
    def __init__(self):
        self.sent: list[dict] = []
        self.answers: list[dict] = []
        self.fail_queue: list[MaxApiError] = []
        self._mid = itertools.count(1)

    async def send_message(self, text, *, chat_id=None, user_id=None, attachments=None, fmt=None, notify=True):
        if self.fail_queue:
            raise self.fail_queue.pop(0)
        self.sent.append({"text": text, "chat_id": chat_id, "user_id": user_id, "attachments": attachments})
        return {"message": {"body": {"mid": f"mid{next(self._mid)}", "text": text},
                            "recipient": {"chat_id": chat_id}}}

    async def answer_callback(self, callback_id, *, message=None, notification=None):
        self.answers.append({"callback_id": callback_id, "message": message, "notification": notification})
        return {"success": True}

    def buttons(self, idx=-1) -> list[str]:
        atts = self.sent[idx]["attachments"] or []
        return [b["payload"] if b["type"] == "callback" else b["type"]
                for a in atts for row in a["payload"]["buttons"] for b in row]


class FakeRepo:
    def __init__(self):
        self.employees: list = []
        self.staff: list = []
        self.consents: list = []
        self.messages: list = []
        self.tickets: list = []
        self.settings: dict = {}

    def add_employee(self, **kw):
        base = {"id": len(self.employees) + 1, "full_name": "Анна Смирнова (синтетика)", "first_name": "Анна",
                    "site_code": "GrPP", "work_type": "office", "recruiter_id": 7, "address_form": None, "max_user_id": None,
                    "chat_id": None, "invite_code": "INV-anna1", "status": "waiting", "dialog_state": None,
                    "dialog_data": {}, "pending_question_id": None, "is_test": False}
        base.update(kw)
        e = SimpleNamespace(**base)
        self.employees.append(e)
        return e

    def add_staff(self, **kw):
        base = {"id": len(self.staff) + 1, "full_name": "Ольга HR (синтетика)", "role": "hr", "site_code": "GrPP",
                    "max_user_id": None, "chat_id": None, "bind_code": "STF-olga1"}
        base.update(kw)
        s = SimpleNamespace(**base)
        self.staff.append(s)
        return s

    async def employee_by_invite(self, code):
        return next((e for e in self.employees if e.invite_code == code), None)

    async def staff_by_bind(self, code):
        return next((s for s in self.staff if s.bind_code == code), None)

    async def employee_by_user(self, uid):
        return next((e for e in self.employees if e.max_user_id == uid), None)

    async def staff_by_user(self, uid):
        return next((s for s in self.staff if s.max_user_id == uid), None)

    async def add_consent(self, employee_id, purpose, given, text_version="v1"):
        self.consents.append(SimpleNamespace(employee_id=employee_id, purpose=purpose, given=given))

    async def add_message(self, **kw):
        self.messages.append(SimpleNamespace(**kw))

    async def create_ticket(self, **kw):
        t = SimpleNamespace(id=len(self.tickets) + 1, status="new", **kw)
        self.tickets.append(t)
        return t

    async def get_setting(self, key):
        return self.settings.get(key)

    async def set_setting(self, key, value):
        self.settings[key] = value

    async def flush(self):
        pass


async def _no_sleep(_):
    return None


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def ctx(api):
    return BotCtx(sender=Sender(api, sleep=_no_sleep), bot_name="korsik_test_bot", miniapp_url="")


# --- конструкторы событий ---

def ev_started(user_id=100, chat_id=500, payload=None):
    return {"update_type": "bot_started", "chat_id": chat_id, "user": {"user_id": user_id, "name": "U"},
            "payload": payload}


def ev_text(text, user_id=100, chat_id=500):
    return {"update_type": "message_created",
            "message": {"sender": {"user_id": user_id, "name": "U"},
                        "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
                        "body": {"mid": "in1", "text": text}}}


def ev_cb(payload, user_id=100, chat_id=500, text="вопрос"):
    return {"update_type": "message_callback",
            "callback": {"callback_id": f"cb-{payload}", "payload": payload, "user": {"user_id": user_id}},
            "message": {"recipient": {"chat_id": chat_id, "chat_type": "dialog"}, "body": {"mid": "m", "text": text}}}
