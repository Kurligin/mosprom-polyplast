"""Чаты команды в боте: учёт групп, HR-группа кнопкой, новичок в чаты в первый день, ссылки, представление.

Тестовая БД и фейковый API «МАКС» (conftest.py). Люди и чаты синтетические.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.bot.api import MaxApiError
from app.bot.handlers import handle_update
from app.bot.repo import SqlRepo
from app.chats import service
from app.chats import texts as T
from app.db.models import Employee, Setting, TeamChat
from app.scenario import engine
from app.scenario import texts as ST
from tests.chats.conftest import (
    DAY0,
    DAY1,
    PRIVACY,
    ev_added,
    ev_press,
    make_chat,
    make_employee,
    shift_to,
)

pytestmark = pytest.mark.asyncio


async def feed(db, ctx, upd: dict) -> None:
    """Событие «МАКС» — одна транзакция, как в runner.process_update."""
    async with db() as s:
        await handle_update(ctx, SqlRepo(s), upd)
        await s.commit()


async def hr_setting(db):
    async with db() as s:
        row = await s.get(Setting, service.HR_KEY)
        return row.value if row else None


async def add(db, *objs) -> list[int]:
    async with db() as s:
        s.add_all(objs)
        await s.commit()
        return [getattr(o, "id", None) or getattr(o, "chat_id", None) for o in objs]


async def emp_data(db, emp_id: int) -> dict:
    async with db() as s:
        return dict((await s.get(Employee, emp_id)).dialog_data or {})


# --- учёт чатов и HR-группа ---------------------------------------------------------------------

async def test_bot_added_registers_chat_and_asks_for_hr_group(db, ctx, api):
    await feed(db, ctx, ev_added(-900))
    async with db() as s:
        chat = await s.get(TeamChat, -900)
    assert chat.active and chat.audience == "none", "новый чат никому не нужен, пока HR не настроил"
    assert chat.title == "Чат -900 (синтетика)" and chat.link and chat.members_count == 7
    assert chat.added_by == 201
    (msg,) = api.to(-900)
    assert msg["text"] == T.GROUP_PROMPT and [b["payload"] for b in api.buttons(msg)] == ["ch:hr:-900"]
    assert chat.prompt_mid == msg["mid"]
    assert await hr_setting(db) is None, "HR-группа не назначается сама"


async def test_hr_presses_button_group_becomes_hr_group_in_place(db, ctx, api):
    await feed(db, ctx, ev_added(-900))
    (prompt,) = api.to(-900)
    await feed(db, ctx, ev_press(201, -900, prompt, "ch:hr:-900"))
    assert await hr_setting(db) == -900
    ans = api.answers[-1]
    assert ans["message"]["text"] == T.HR_GROUP_DONE and ans["message"]["attachments"] == []
    assert len(api.to(-900)) == 1, "ответ на кнопку — правка того же сообщения"
    async with db() as s:
        chat = await s.get(TeamChat, -900)
        assert chat.prompt_mid is None and chat.audience == "none"
    # уведомления для HR (runtime.notify_hr_group и модули) читают тот же Setting
    async with db() as s:
        assert await SqlRepo(s).get_setting("hr_group_chat_id") == -900


@pytest.mark.parametrize("user_id", [999, 100, 203], ids=["не подключён", "новичок", "руководитель"])
async def test_not_hr_gets_toast(db, ctx, api, user_id):
    await add(db, make_employee())
    await feed(db, ctx, ev_added(-900))
    (prompt,) = api.to(-900)
    await feed(db, ctx, ev_press(user_id, -900, prompt, "ch:hr:-900"))
    assert api.answers[-1]["notification"] == T.ONLY_HR and api.answers[-1]["message"] is None
    assert await hr_setting(db) is None
    async with db() as s:
        e = await s.scalar(select(Employee))
        assert e.chat_id == 500, "нажатие в группе не меняет личный чат новичка"


async def test_group_press_does_not_become_personal_chat(db, ctx, api):
    """Новичок без личного чата жмёт кнопку в группе: группа не становится его чатом с ботом."""
    await add(db, make_employee(chat_id=None))
    await feed(db, ctx, ev_added(-900))
    await feed(db, ctx, ev_press(100, -900, api.to(-900)[0], "ch:hr:-900"))
    async with db() as s:
        assert (await s.scalar(select(Employee))).chat_id is None


async def test_after_hr_group_new_chat_gets_hello_and_old_prompts_lose_button(db, ctx, api):
    await feed(db, ctx, ev_added(-900))
    await feed(db, ctx, ev_added(-910))
    (prompt_hr,), (prompt_team,) = api.to(-900), api.to(-910)
    await feed(db, ctx, ev_press(201, -900, prompt_hr, "ch:hr:-900"))
    # кнопка в другой группе больше не нужна: то же сообщение без кнопки
    assert [(e["mid"], e["text"], e["attachments"]) for e in api.edits] == [(prompt_team["mid"], T.TEAM_HELLO, [])]
    await feed(db, ctx, ev_added(-920))
    (hello,) = api.to(-920)
    assert hello["text"] == T.TEAM_HELLO and not api.buttons(hello)


async def test_bot_removed_turns_chat_off_and_clears_hr_group(db, ctx, api):
    await feed(db, ctx, ev_added(-900))
    await feed(db, ctx, ev_press(201, -900, api.to(-900)[0], "ch:hr:-900"))
    await feed(db, ctx, {"update_type": "bot_removed", "chat_id": -900, "user": {"user_id": 201}, "is_channel": False})
    async with db() as s:
        assert not (await s.get(TeamChat, -900)).active
    assert await hr_setting(db) is None
    # бота вернули: чат снова в учёте, HR-группы нет — снова кнопка
    await feed(db, ctx, ev_added(-900))
    async with db() as s:
        assert (await s.get(TeamChat, -900)).active
    assert api.buttons(api.to(-900)[-1])[0]["payload"] == "ch:hr:-900"


async def test_channel_is_ignored(db, ctx, api):
    await feed(db, ctx, {**ev_added(-930), "is_channel": True})
    async with db() as s:
        assert await s.get(TeamChat, -930) is None
    assert not api.sent


# --- новичок в чаты ---------------------------------------------------------------------------------

async def setup_chats(db):
    """Общий чат, чаты площадок, офиса и производства, HR-группа."""
    async with db() as s:
        s.add_all([make_chat(-1, title="Общий чат"),
                   make_chat(-2, title="GTP", audience="sites", sites=["GTP"]),
                   make_chat(-3, title="GrPP", audience="sites", sites=["GrPP"]),
                   make_chat(-4, title="Офис", work_type="office"),
                   make_chat(-5, title="Производство", work_type="production"),
                   make_chat(-6, title="Никому", audience="none"),
                   make_chat(-7, title="Удалённый", active=False),
                   make_chat(-9, title="HR")])
        await SqlRepo(s).set_setting(service.HR_KEY, -9)
        await s.commit()


async def test_first_day_adds_by_site_and_work_type(db, sender, api):
    await setup_chats(db)
    (emp_id,) = await add(db, make_employee())
    await service.tick(DAY0, sender=sender, session_factory=db)
    assert api.members == [], "до первого рабочего дня не добавляем"
    stats = await service.tick(DAY1, sender=sender, session_factory=db)
    assert sorted(c for c, _ in api.members) == [-5, -2, -1] and all(u == [100] for _, u in api.members)
    assert stats["added"] == 3 and not api.to(500), "всё получилось: новичку лишних сообщений нет"
    chats = (await emp_data(db, emp_id))["chats"]
    assert {k: v["status"] for k, v in chats.items()} == {"-1": "added", "-2": "added", "-5": "added"}
    assert chats["-2"]["title"] == "GTP" and chats["-2"]["at"]


async def test_office_newcomer_other_site(db, sender, api):
    await setup_chats(db)
    await add(db, make_employee(site_code="GrPP", work_type="office"))
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert sorted(c for c, _ in api.members) == [-4, -3, -1]


async def test_idempotent_once_per_chat(db, sender, api):
    await setup_chats(db)
    (emp_id,) = await add(db, make_employee())
    await service.tick(DAY1, sender=sender, session_factory=db)
    await service.tick(DAY1 + timedelta(hours=1), sender=sender, session_factory=db)
    async with db() as s:
        emp = await s.get(Employee, emp_id)
        await service.add_to_chats(s, emp, sender=sender, now=DAY1)
        await s.commit()
    assert len(api.members) == 3
    # HR включил новый чат — добавим только в него
    async with db() as s:
        (await s.get(TeamChat, -6)).audience = "all"
        await s.commit()
    await service.tick(DAY1 + timedelta(hours=2), sender=sender, session_factory=db)
    assert [c for c, _ in api.members[3:]] == [-6]


async def test_privacy_refusal_one_message_with_links(db, sender, api):
    await setup_chats(db)
    async with db() as s:
        (await s.get(TeamChat, -5)).link = None  # у чата нет ссылки-приглашения
        await s.commit()
    api.member_result = {-1: PRIVACY, -2: {"success": True}, -5: PRIVACY}
    (emp_id,) = await add(db, make_employee())
    await service.tick(DAY1, sender=sender, session_factory=db)
    (msg,) = api.to(500)
    assert msg["text"] == ("Вот чаты твоей команды. В чат «GTP» я тебя уже добавил, а сюда сам не смог: "
                           "так настроена приватность в «МАКС». Вступи по кнопке ниже, там коллеги и все новости.")
    assert [(b["type"], b["text"], b["url"]) for b in api.buttons(msg)] == [
        ("link", "Общий чат", "https://max.ru/join/chat1")]
    assert "—" not in msg["text"] and msg["fmt"] is None
    chats = (await emp_data(db, emp_id))["chats"]
    assert chats["-1"]["status"] == "link" and chats["-1"]["mid"] == msg["mid"]
    assert chats["-1"]["error"] == "add.participant.privacy"
    assert chats["-5"]["status"] == "failed" and chats["-2"]["status"] == "added"
    await service.tick(DAY1 + timedelta(hours=1), sender=sender, session_factory=db)
    assert len(api.to(500)) == 1, "ссылки — одним сообщением и один раз"


async def test_vy_and_no_added(db, sender, api):
    async with db() as s:
        s.add(make_chat(-1, title="Общий чат"))
        await s.commit()
    api.member_result = {-1: MaxApiError(403, "chat.denied", "not admin")}
    await add(db, make_employee(address_form="vy"))
    await service.tick(DAY1, sender=sender, session_factory=db)
    (msg,) = api.to(500)
    assert msg["text"] == ("Вот чаты вашей команды. Сам добавить вас не смог. "
                           "Вступите по кнопке ниже, там коллеги и все новости.")


async def test_links_text_many():
    emp = make_employee()
    assert service.links_text(emp, ["A", "B", "C"], True, 2) == (
        "Вот чаты твоей команды. В чаты «A», «B» и «C» я тебя уже добавил, а сюда сам не смог: "
        "так настроена приватность в «МАКС». Вступи по кнопкам ниже, там коллеги и все новости.")


async def test_network_error_retried_later(db, sender, api):
    async with db() as s:
        s.add(make_chat(-1, title="Общий чат"))
        await s.commit()
    api.member_result = {-1: MaxApiError(502, "bad.gateway")}
    (emp_id,) = await add(db, make_employee())
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert "-1" not in ((await emp_data(db, emp_id)).get("chats") or {}) and not api.to(500)
    api.member_result = {}
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert (await emp_data(db, emp_id))["chats"]["-1"]["status"] == "added"


async def test_synthetic_and_not_onboarded_untouched(db, sender, api):
    await setup_chats(db)
    await add(db, make_employee(is_synthetic=True, is_test=False),
              make_employee(max_user_id=101, chat_id=501, invite_code="INV-2", dialog_data={}),
              make_employee(max_user_id=None, chat_id=None, invite_code="INV-3"))
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert api.members == [] and api.sent == []


async def test_bot_off_job_does_nothing(db, api):
    await setup_chats(db)
    await add(db, make_employee())
    assert (await service.tick(DAY1, session_factory=db))["added"] == 0  # контекста бота нет
    assert api.members == []


async def test_onboarding_on_first_day_adds_right_away(db, ctx, api, monkeypatch):
    """Знакомство в день выхода: сначала шаги сценария (обработчик сценария), потом чаты."""
    from app.chats import hooks as chats_hooks

    async with db() as s:
        s.add(make_chat(-1, title="Общий чат"))
        await s.commit()
    (emp_id,) = await add(db, make_employee(time_shift_hours=shift_to(DAY1)))
    calls = []

    async def scenario_first(ctx_, repo, emp):
        calls.append("scenario")

    monkeypatch.setattr(chats_hooks, "_prev", scenario_first)
    async with db() as s:
        await chats_hooks.after_onboarding(ctx, SqlRepo(s), await s.get(Employee, emp_id))
        await s.commit()
    assert calls == ["scenario"] and api.members == [(-1, [100])]
    assert (await emp_data(db, emp_id))["chats"]["-1"]["status"] == "added"


async def test_onboarding_before_first_day_waits(db, ctx, api):
    async with db() as s:
        s.add(make_chat(-1, title="Общий чат"))
        await s.commit()
    (emp_id,) = await add(db, make_employee(time_shift_hours=shift_to(DAY0)))
    async with db() as s:
        await service.on_onboarded(ctx, SqlRepo(s), await s.get(Employee, emp_id))
        await s.commit()
    assert api.members == []


# --- «представлю тебя коллегам» -------------------------------------------------------------------------

INTRO = "Я Тест, три года работал на линии экструзии. Люблю походы и шахматы."
PHOTO = {"type": "image", "payload": {"url": "https://example.invalid/test.jpg"}}


async def tell_about_me(db, ctx, emp_id: int, *, photo: bool = True) -> None:
    """Новичок присылает рассказ о себе и фото после просьбы «представлю тебя коллегам»."""
    async with db() as s:
        emp = await s.get(Employee, emp_id)
        engine._set_data(emp, **{engine.INTRO_WAIT: {"since": engine._vnow(emp).isoformat(), "asked": []}})
        await s.commit()
    msgs = [(INTRO, {})] + ([("", {"attachments": [PHOTO]})] if photo else [])
    for text, body in msgs:
        async with db() as s:
            repo = SqlRepo(s)
            emp = await repo.employee_by_user(100)
            assert await engine.on_intro(ctx, repo, emp, text, body)
            await s.commit()


async def intro_setup(db) -> int:
    async with db() as s:
        s.add_all([make_chat(-1, title="Общий чат"), make_chat(-2, title="Команда GTP", audience="sites", sites=["GTP"]),
                   make_chat(-9, title="HR")])
        await SqlRepo(s).set_setting(service.HR_KEY, -9)
        await s.commit()
    (emp_id,) = await add(db, make_employee(full_name="Тестов Тест Тестович (синтетика)"))
    return emp_id


async def test_intro_asks_consent_in_thanks_message_and_publishes(db, ctx, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx, emp_id)
    ask = api.to(500)[-1]
    assert ask["text"] == (ST.INTRO_THANKS[0] + "\n\nХочешь, я сам представлю тебя коллегам в чате «Команда GTP»? "
                           "Опубликую твой рассказ и фото, так тебя узнают ещё до встречи.")
    assert [(b["text"], b["payload"]) for b in api.buttons(ask)] == [
        ("Опубликовать в чате команды", "ch:pub:-2"), ("Не нужно", "ch:nopub")]
    assert not api.to(-2), "без согласия в чат ничего не уходит"
    sends = len(api.sent)
    await feed(db, ctx, ev_press(100, 500, ask, "ch:pub:-2", chat_type="dialog"))
    (post,) = api.to(-2)
    assert post["text"] == f"Знакомьтесь, это Тест Тестов! Теперь работает с нами на площадке GTP.\n\n{INTRO}"
    assert post["attachments"] == [PHOTO] and post["fmt"] is None
    assert len(api.sent) == sends + 1, "новичку новых сообщений нет: ответ правит то же сообщение"
    done = api.answers[-1]["message"]
    assert done["text"].endswith("✅ Опубликовать в чате команды\n\nГотово, опубликовал в чате «Команда GTP». "
                                 "Загляни туда, коллеги наверняка уже здороваются!")
    assert done["attachments"] == []
    post_data = (await emp_data(db, emp_id))["intro_post"]
    assert post_data["status"] == "published" and post_data["chat_id"] == -2 and post_data["post_mid"] == post["mid"]
    # повторное нажатие (старая кнопка) второй публикации не делает
    await feed(db, ctx, ev_press(100, 500, ask, "ch:pub:-2", chat_type="dialog"))
    assert len(api.to(-2)) == 1


async def test_intro_declined_never_published(db, ctx, sender, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx, emp_id)
    ask = api.to(500)[-1]
    await feed(db, ctx, ev_press(100, 500, ask, "ch:nopub", chat_type="dialog"))
    assert api.answers[-1]["message"]["text"].endswith("✅ Не нужно\n\nХорошо, не публикую. "
                                                       "Рекрутер представит тебя коллегам сам.")
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert not api.to(-2) and not api.to(-1)
    assert len(api.to(500)) == 2, "после отказа бот не переспрашивает"
    assert (await emp_data(db, emp_id))["intro_post"]["status"] == "declined"


async def test_intro_without_team_chat_plain_thanks(db, ctx, api):
    (emp_id,) = await add(db, make_employee())
    await tell_about_me(db, ctx, emp_id)
    assert api.to(500)[-1]["text"] == ST.INTRO_THANKS[0] and not api.buttons(api.to(500)[-1])
    assert "intro_post" not in await emp_data(db, emp_id)


async def test_text_only_intro_offered_by_job_after_wait(db, ctx, sender, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx, emp_id, photo=False)
    await service.tick(sender=sender, session_factory=db)
    assert not any(api.buttons(m) for m in api.to(500)), "пока ждём фото, не спрашиваем"
    later = datetime.now(UTC) + engine.INTRO_TTL + timedelta(hours=1)  # фото так и не пришло
    await service.tick(later, sender=sender, session_factory=db)
    ask = api.to(500)[-1]
    assert ask["text"].startswith("Хочешь, я сам представлю тебя коллегам в чате «Команда GTP»? Опубликую твой рассказ,")
    await service.tick(later, sender=sender, session_factory=db)
    assert api.to(500)[-1] is ask, "спрашиваем один раз"


async def test_synthetic_not_offered(db, ctx, sender, api):
    await intro_setup(db)
    async with db() as s:
        emp = await s.scalar(select(Employee))
        emp.is_synthetic = True
        engine._set_data(emp, intro={"text": INTRO, "photo_url": None, "at": "2026-11-03T10:00:00+00:00"})
        await s.commit()
        assert not await service.offer_intro(sender, SqlRepo(s), emp)
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert api.sent == []


async def test_intro_chat_priority():
    emp = make_employee()
    general = make_chat(-1)
    site = make_chat(-2, audience="sites", sites=["GTP", "GrPP"])
    narrow = make_chat(-3, audience="sites", sites=["GTP"], work_type="production")
    other = make_chat(-4, audience="sites", sites=["GrPP"], for_intro=True)
    news = make_chat(-5, audience="none", for_intro=True)
    hr = make_chat(-9, audience="none", for_intro=True)
    assert service.intro_chat([general], emp, None) is None, "общий чат — только с отметкой «для представлений»"
    assert service.intro_chat([general, site, narrow, other], emp, None) is narrow
    assert service.intro_chat([general, site, narrow, news], emp, None) is news
    assert service.intro_chat([hr], emp, -9) is None, "в HR-группу не публикуем"


async def test_display_name():
    assert service.display_name(make_employee(full_name="Смирнов Иван Петрович (синтетика)", first_name="Иван")) == \
        "Иван Смирнов"
    assert service.display_name(make_employee(full_name="Иван Смирнов", first_name="Иван")) == "Иван Смирнов"
