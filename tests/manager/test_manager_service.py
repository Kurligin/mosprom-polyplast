"""Руководитель: план встреч, время push, тексты, кнопка «Провёл», напоминание про доступы."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.bot import runtime
from app.db.models import Employee, ManagerTask, Staff
from app.manager import hooks as mhooks
from app.manager import service as S
from app.manager import texts as T

MSK = ZoneInfo("Europe/Moscow")


def emp_(**kw):
    base = {"id": 1, "full_name": "Петров Иван Сергеевич", "first_name": "Иван", "site_code": "GTP",
            "start_date": date(2026, 10, 19), "probation_end": date(2027, 1, 19), "manager_id": 10,
            "time_shift_hours": 0, "is_test": False, "is_synthetic": False, "max_user_id": None,
            "dialog_data": {}, "work_type": "office", "status": "active"}
    base.update(kw)
    return SimpleNamespace(**base)


def task_(**kw):
    base = {"id": 5, "manager_id": 10, "employee_id": 1, "day": 3, "kind": S.KIND_MEETING,
            "due_date": date(2026, 10, 21), "status": "pending", "mid": None, "done_at": None}
    base.update(kw)
    return SimpleNamespace(**base)


def at(y, m, d, h=10, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=MSK)


# --- план встреч -------------------------------------------------------------

def test_plan_from_monday():
    plan = S.plan_meetings(date(2026, 10, 19), date(2027, 1, 19))
    assert [(m.kind, m.day) for m in plan][:3] == [("meeting", 3), ("meeting", 30), ("meeting", 60)]
    assert plan[0].due == date(2026, 10, 21)          # третий рабочий день
    assert plan[1].due == date(2026, 11, 17)          # 30-й день — вторник
    assert plan[2].due == date(2026, 12, 17)          # 60-й день — четверг
    final = plan[3]
    assert final.kind == S.KIND_PROBATION and final.due == date(2027, 1, 14)
    assert final.day == (date(2027, 1, 14) - date(2026, 10, 19)).days + 1


def test_plan_skips_holidays_and_weekends():
    # выход в воскресенье: рабочие дни пн, вт, ср → третий — среда
    assert S.plan_meetings(date(2026, 10, 25), None)[0].due == date(2026, 10, 28)
    # 4 ноября — праздник
    assert S.plan_meetings(date(2026, 11, 2), None)[0].due == date(2026, 11, 5)
    # 30-й день выпал на субботу → понедельник
    start = date(2026, 10, 2)
    assert (start + timedelta(days=29)).weekday() == 5
    assert S.plan_meetings(start, None)[1].due == date(2026, 11, 2)


def test_final_meeting_moves_to_previous_workday():
    # конец испытательного 2026-11-09 (пн) → −5 = 4 ноября (праздник) → 3 ноября
    plan = S.plan_meetings(date(2026, 8, 10), date(2026, 11, 9))
    assert plan[-1].kind == S.KIND_PROBATION and plan[-1].due == date(2026, 11, 3)


def test_short_probation_drops_late_meetings():
    plan = S.plan_meetings(date(2026, 10, 19), date(2026, 11, 19))
    assert (plan[0].kind, plan[0].day) == ("meeting", 3)
    assert [m.kind for m in plan] == ["meeting", "probation"]


# --- когда слать push -------------------------------------------------------

def test_push_at_ten_on_due_day():
    e, t = emp_(), task_()
    assert not S.push_is_due(t, e, at(2026, 10, 20, 12))       # накануне
    assert not S.push_is_due(t, e, at(2026, 10, 21, 9, 59))
    assert S.push_is_due(t, e, at(2026, 10, 21, 10, 0))
    assert S.push_is_due(t, e, at(2026, 10, 22, 11))            # пропустили — догоняем
    assert not S.push_is_due(t, e, at(2026, 10, 22, 8))         # но не ночью
    assert not S.push_is_due(t, e, at(2026, 10, 24, 11))        # не в субботу
    assert not S.push_is_due(t, e, at(2026, 10, 26, 11))        # прошло больше 3 дней


def test_push_with_time_travel_on_calendar_day():
    # выход в воскресенье, перемотка на 3-й календарный день (вт), встреча — 3-й рабочий (ср)
    e = emp_(start_date=date(2026, 10, 25), time_shift_hours=96)
    t = task_(due_date=date(2026, 10, 28))
    assert S.push_is_due(t, e, at(2026, 10, 27, 10, 30))
    assert not S.push_is_due(t, e, at(2026, 10, 26, 12))
    # без перемотки — строго в день встречи
    assert not S.push_is_due(t, emp_(start_date=date(2026, 10, 25)), at(2026, 10, 27, 10, 30))


def test_final_push_until_probation_end():
    e = emp_(probation_end=date(2027, 1, 19))
    t = task_(kind=S.KIND_PROBATION, day=88, due_date=date(2027, 1, 14))
    assert S.push_is_due(t, e, at(2027, 1, 19, 11))
    assert not S.push_is_due(t, e, at(2027, 1, 20, 11))


def test_employee_now_uses_shift():
    e = emp_(time_shift_hours=48)
    now = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
    assert S.employee_now(e, now) == datetime(2026, 10, 25, 10, 0, tzinfo=MSK)
    assert S.adaptation_day(emp_(start_date=date(2026, 10, 23)), now) == 1


# --- тексты -----------------------------------------------------------------

def test_display_name():
    assert S.display_name(emp_()) == "Иван Петров"
    assert S.display_name(emp_(full_name="Иван Петров")) == "Иван Петров"
    assert S.display_name(emp_(full_name="Синтетиков Демьян [синтетика]", first_name="Демьян")) == "Демьян Синтетиков"


def test_push_view(monkeypatch):
    monkeypatch.setattr(S.settings, "miniapp_url", "https://example.test")
    monkeypatch.setattr(S.settings, "max_bot_name", "@korsik_bot")
    text, atts = S.push_view(task_(), emp_())
    assert text.startswith("**Иван Петров** работает у нас третий день")
    assert "15 минут" in text and "—" not in text
    buttons = atts[0]["payload"]["buttons"][0]
    assert buttons[0] == {"type": "callback", "text": "Провёл", "payload": "mg:done:5"}
    assert buttons[1]["type"] == "open_app" and buttons[1]["web_app"] == "korsik_bot"


def test_push_view_without_miniapp(monkeypatch):
    monkeypatch.setattr(S.settings, "miniapp_url", "")
    _, atts = S.push_view(task_(day=30, due_date=date(2026, 11, 17)), emp_())
    assert [b["type"] for b in atts[0]["payload"]["buttons"][0]] == ["callback"]


def test_probation_push_text():
    t = task_(kind=S.KIND_PROBATION, day=88, due_date=date(2027, 1, 14))
    text, _ = S.push_view(t, emp_(), at(2027, 1, 14, 10))
    assert "заканчивает испытательный срок 19 января, через 5 дней" in text


def test_probation_when():
    assert T.probation_when(date(2027, 1, 19), date(2027, 1, 18)) == "завтра, 19 января"
    assert T.probation_when(date(2027, 1, 19), date(2027, 1, 19)) == "сегодня"
    assert T.probation_when(date(2027, 1, 19), date(2027, 1, 17)) == "19 января, через 2 дня"


def test_meeting_dict_matches_miniapp():
    assert S.meeting_dict(task_()) == {"id": 5, "day": 3, "title": "Встреча на 3-й день",
                                       "date": "2026-10-21", "done": False}
    final = task_(kind=S.KIND_PROBATION, day=88, status="done")
    assert S.meeting_dict(final)["title"] == "Итоговая встреча" and S.meeting_dict(final)["done"]


# --- синхронизация встреч ----------------------------------------------------

class FakeSession:
    def __init__(self, objects=()):
        self.objects = {(type(o), o.id): o for o in objects}
        self.added: list = []
        self.deleted: list = []

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 1000 + len(self.added)
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass

    async def get(self, cls, pk):
        return self.objects.get((cls, pk))


@pytest.mark.asyncio
async def test_sync_creates_plan_and_is_idempotent():
    s, e = FakeSession(), emp_()
    tasks, created = await S._sync_tasks(s, e, [])
    assert created == 4 and [(t.kind, t.day) for t in tasks][:3] == [("meeting", 3), ("meeting", 30), ("meeting", 60)]
    tasks2, created2 = await S._sync_tasks(s, e, tasks)
    assert created2 == 0 and [t.id for t in tasks2] == [t.id for t in tasks]


@pytest.mark.asyncio
async def test_sync_follows_manager_and_dates():
    s, e = FakeSession(), emp_()
    tasks, _ = await S._sync_tasks(s, e, [])
    tasks[0].status = "done"
    e.manager_id = 11
    e.probation_end = date(2027, 1, 26)
    new, created = await S._sync_tasks(s, e, tasks)
    # непроведённые встречи старого руководителя удалены, проведённая осталась в истории
    assert {t.id for t in s.deleted} == {t.id for t in tasks[1:]}
    assert created == 4 and all(t.manager_id == 11 for t in new)
    assert new[-1].due_date == date(2027, 1, 21)


def test_rewind_resets_future_tasks_for_test_employee():
    e = emp_(is_test=True)
    t = task_(mid="m1", status="done", done_at=datetime.now(UTC))
    S._rewind([t], e, at(2026, 10, 17, 12))
    assert (t.status, t.mid, t.done_at) == ("pending", None, None)


# --- кнопка «Провёл» --------------------------------------------------------

class FakeCb:
    def __init__(self, payload, session, *, is_staff=True):
        self.payload = payload
        self.repo = SimpleNamespace(s=session)
        self.is_staff = is_staff
        self.replaced = None
        self.toasted = None

    async def replace(self, text, attachments=None, **kw):
        self.replaced = (text, attachments)

    async def toast(self, text):
        self.toasted = text


def _staff(**kw):
    base = {"id": 10, "role": "manager", "full_name": "Руководитель (синтетика)", "chat_id": 77, "max_user_id": 7}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_done_button_marks_and_replaces(monkeypatch):
    monkeypatch.setattr(S.settings, "miniapp_url", "")
    t = task_()
    e = emp_()
    s = FakeSession([t])
    s.objects[(Employee, 1)] = e
    s.objects[(ManagerTask, 5)] = t
    cb = FakeCb("mg:done:5", s)
    await mhooks.manager_button(cb, _staff())
    assert t.status == "done" and t.done_at is not None
    assert cb.replaced[0] == "Встреча проведена ✓\n**Иван Петров**, первые дни в компании. Спасибо, что нашли время!"
    assert cb.replaced[1] == []


@pytest.mark.asyncio
async def test_done_button_foreign_and_stale():
    t = task_()
    s = FakeSession()
    s.objects[(ManagerTask, 5)] = t
    cb = FakeCb("mg:done:5", s)
    await mhooks.manager_button(cb, _staff(id=99))
    assert cb.toasted == T.NOT_YOURS and t.status == "pending"
    cb = FakeCb("mg:oops", s)
    await mhooks.manager_button(cb, _staff())
    assert cb.toasted == T.STALE
    cb = FakeCb("mg:done:404", s)
    await mhooks.manager_button(cb, _staff())
    assert cb.toasted == T.NOT_FOUND


@pytest.mark.asyncio
async def test_mark_done_from_miniapp_edits_push(monkeypatch):
    edits = []

    async def fake_edit(mid, text, attachments=None, **kw):
        edits.append((mid, text))
        return True

    monkeypatch.setattr(runtime, "edit", fake_edit)
    t = task_(mid="m42")
    s = FakeSession()
    s.objects[(Employee, 1)] = emp_()
    await S.mark_done(s, t, _staff(role="hr", id=1))
    assert t.status == "done" and edits[0][0] == "m42" and edits[0][1].startswith("Встреча проведена ✓")


# --- напоминание про доступы -------------------------------------------------

@pytest.mark.asyncio
async def test_remind_access_goes_to_manager_once(monkeypatch):
    sent, hr = [], []

    async def fake_send(repo, text, *, staff=None, attachments=None, **kw):
        sent.append((staff.id, text))
        return ["m1"]

    async def fake_hr(repo, text, attachments=None):
        hr.append(text)
        return ["g1"]

    monkeypatch.setattr(runtime, "send", fake_send)
    monkeypatch.setattr(runtime, "notify_hr_group", fake_hr)
    s = FakeSession()
    s.objects[(Staff, 10)] = _staff()
    repo = SimpleNamespace(s=s)
    e = emp_()
    assert await S.remind_access(repo, e)
    assert sent == [(10, "**Иван Петров** пишет, что пока нет всех доступов. Проверьте, пожалуйста.")]
    assert "access_reminded_at" in e.dialog_data
    assert await S.remind_access(repo, e)   # повторное нажатие не дублирует push
    assert len(sent) == 1 and hr == []


@pytest.mark.asyncio
async def test_remind_access_falls_back_to_hr(monkeypatch):
    hr = []

    async def fake_hr(repo, text, attachments=None):
        hr.append(text)
        return ["g1"]

    monkeypatch.setattr(runtime, "notify_hr_group", fake_hr)
    s = FakeSession()
    s.objects[(Staff, 10)] = _staff(chat_id=None, max_user_id=None)
    assert await S.remind_access(SimpleNamespace(s=s), emp_())
    assert hr and "Руководитель ещё не подключён" in hr[0]
