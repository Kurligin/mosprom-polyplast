"""Чаты команды: случаи из ревью. Старая кнопка HR-группы, публикация рассказа по старому вопросу и в чужой чат,
ошибки addMembers, откат добавлений при сбое вопроса, название площадки в посте.

Тестовая БД и фейковый API «МАКС» (conftest.py). Люди и чаты синтетические.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.bot.api import MaxApiError
from app.bot.handlers import handle_update
from app.bot.repo import SqlRepo
from app.chats import service
from app.chats import texts as T
from app.db.models import Employee, Setting, Site, Staff, TeamChat
from app.scenario import engine
from app.scenario import texts as ST
from tests.chats.conftest import DAY1, ev_added, ev_press, make_chat, make_employee

pytestmark = pytest.mark.asyncio

INTRO = "Я Тест, три года работал на линии экструзии. Люблю походы и шахматы."
PHOTO = {"type": "image", "payload": {"url": "https://example.invalid/test.jpg"}}
RECRUITER = 202


async def feed(db, ctx, upd: dict) -> None:
    async with db() as s:
        await handle_update(ctx, SqlRepo(s), upd)
        await s.commit()


async def hr_setting(db):
    async with db() as s:
        row = await s.get(Setting, service.HR_KEY)
        return row.value if row else None


async def emp_data(db, emp_id: int) -> dict:
    async with db() as s:
        return dict((await s.get(Employee, emp_id)).dialog_data or {})


async def add(db, *objs) -> list[int]:
    async with db() as s:
        s.add_all(objs)
        await s.commit()
        return [getattr(o, "id", None) or getattr(o, "chat_id", None) for o in objs]


def removed(chat_id: int) -> dict:
    return {"update_type": "bot_removed", "chat_id": chat_id, "user": {"user_id": 201}, "is_channel": False}


def answered(api, msg: dict) -> dict:
    """Сообщение после ответа на кнопку: текст и клавиатура из последнего answer_callback."""
    return {**msg, "text": api.answers[-1]["message"]["text"], "attachments": api.answers[-1]["message"]["attachments"]}


# --- 1. старая кнопка «Сделать эту группу HR-группой» ---------------------------------------------------

async def recruiter(db) -> None:
    await add(db, Staff(full_name="Подборова Мария (рекрутер, синтетика)", role="recruiter",
                        max_user_id=RECRUITER, chat_id=602))


async def test_old_hr_button_does_not_move_hr_group_into_team_chat(db, ctx, api, sender):
    """Бота удалили из A (кнопка осталась живой), HR выбрал B в панели, бота вернули в A, рекрутер жмёт старую кнопку."""
    await recruiter(db)
    await feed(db, ctx, ev_added(-900))
    (old_prompt,) = api.to(-900)
    await feed(db, ctx, removed(-900))
    await feed(db, ctx, ev_added(-910))
    async with db() as s:
        await service.update_chat(s, await s.get(TeamChat, -910), fields={"is_hr_group": True}, sender=sender)
        await s.commit()
    await feed(db, ctx, ev_added(-900))
    assert api.to(-900)[-1]["text"] == T.TEAM_HELLO
    await feed(db, ctx, ev_press(RECRUITER, -900, old_prompt, "ch:hr:-900"))
    assert await hr_setting(db) == -910, "HR-группа не переехала в чат команды"
    ans = api.answers[-1]
    assert ans["notification"] == T.HR_ALREADY
    assert ans["message"]["text"] == T.TEAM_HELLO and ans["message"]["attachments"] == [], "кнопка снята"


async def test_repeated_bot_added_does_not_send_second_prompt(db, ctx, api):
    await feed(db, ctx, ev_added(-900))
    await feed(db, ctx, ev_added(-900))  # повторная доставка после рестарта
    (prompt,) = api.to(-900)
    async with db() as s:
        assert (await s.get(TeamChat, -900)).prompt_mid == prompt["mid"]


async def test_stale_prompt_without_hr_group_is_not_accepted(db, ctx, api):
    """Пока HR-группы нет, принимаем нажатие только в последнем сообщении с кнопкой."""
    await feed(db, ctx, ev_added(-900))
    await feed(db, ctx, removed(-900))
    await feed(db, ctx, ev_added(-900))
    old, new = api.to(-900)
    await feed(db, ctx, ev_press(201, -900, old, "ch:hr:-900"))
    assert await hr_setting(db) is None
    assert api.answers[-1]["notification"] == T.HR_PROMPT_STALE
    await feed(db, ctx, ev_press(201, -900, new, "ch:hr:-900"))
    assert await hr_setting(db) == -900


async def test_failed_edit_leaves_button_but_press_does_not_reassign(db, ctx, api):
    """set_hr_group обнулил prompt_mid, а правка не прошла: кнопка в чате команды жива, но HR-группу не меняет."""
    await feed(db, ctx, ev_added(-900))
    await feed(db, ctx, ev_added(-910))
    (team_prompt,) = api.to(-910)

    async def edit_fails(mid, text, *, attachments=None, fmt=None):
        raise MaxApiError(400, "edit.failed")

    api.edit_message = edit_fails
    await feed(db, ctx, ev_press(201, -900, api.to(-900)[0], "ch:hr:-900"))
    assert await hr_setting(db) == -900
    await feed(db, ctx, ev_press(201, -910, team_prompt, "ch:hr:-910"))
    assert await hr_setting(db) == -900
    assert api.answers[-1]["notification"] == T.HR_ALREADY


# --- представление коллегам: общие шаги -------------------------------------------------------------

async def intro_setup(db, *chats: TeamChat, hr: int | None = -9, **emp_kw) -> int:
    async with db() as s:
        s.add_all(chats or [make_chat(-1, title="Общий чат"),
                            make_chat(-2, title="Команда GTP", audience="sites", sites=["GTP"])])
        if hr is not None:
            s.add(make_chat(hr, title="HR"))
            await SqlRepo(s).set_setting(service.HR_KEY, hr)
        await s.commit()
    (emp_id,) = await add(db, make_employee(full_name="Тестов Тест Тестович (синтетика)", **emp_kw))
    return emp_id


async def tell_about_me(db, ctx, *, text: str = INTRO, photo: bool = True) -> None:
    async with db() as s:
        emp = await SqlRepo(s).employee_by_user(100)
        engine._set_data(emp, **{engine.INTRO_WAIT: {"since": engine._vnow(emp).isoformat(), "asked": []}})
        await s.commit()
    msgs = [(text, {})] + ([("", {"attachments": [PHOTO]})] if photo else [])
    for t, body in msgs:
        async with db() as s:
            repo = SqlRepo(s)
            emp = await repo.employee_by_user(100)
            assert await engine.on_intro(ctx, repo, emp, t, body)
            await s.commit()


async def press_dialog(db, ctx, msg: dict, payload: str) -> None:
    await feed(db, ctx, ev_press(100, 500, msg, payload, chat_type="dialog"))


# --- 2. согласие на конкретный текст ---------------------------------------------------------------

async def test_offer_shows_exact_post(db, ctx, api):
    await intro_setup(db)
    await tell_about_me(db, ctx)
    ask = api.to(500)[-1]
    post = f"Знакомьтесь, это Тест Тестов! Теперь работает с нами, площадка GTP.\n\n{INTRO}"
    assert ask["text"] == (f"{ST.INTRO_THANKS[0]}\n\nХочешь, я сам представлю тебя коллегам в чате «Команда GTP»? "
                           f"Так тебя узнают ещё до встречи. Вот что опубликую вместе с твоим фото:\n\n{post}")
    assert ask["fmt"] is None, "текст новичка без markdown: как в посте"
    await press_dialog(db, ctx, ask, "ch:pub:-2")
    assert api.to(-2)[-1]["text"] == post, "в чат уходит ровно показанный текст"


# --- 3. старый вопрос после перемотки ----------------------------------------------------------------

async def test_old_question_after_rewind_publishes_nothing(db, ctx, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx)
    ask = api.to(500)[-1]
    async with db() as s:  # перемотка: рассказ забыт (как engine._forget_gates), старое сообщение с кнопками осталось
        emp = await s.get(Employee, emp_id)
        engine._set_data(emp, intro=None)
        await s.commit()
    await press_dialog(db, ctx, ask, "ch:pub:-2")
    assert not api.to(-2), "пустое представление в чат не уходит"
    assert api.answers[-1]["notification"] == T.POST_STALE
    assert api.answers[-1]["message"]["attachments"] == [], "кнопки у старого вопроса сняты"


async def test_old_question_does_not_publish_unfinished_new_intro(db, ctx, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx)
    ask = api.to(500)[-1]
    async with db() as s:
        emp = await s.get(Employee, emp_id)
        engine._set_data(emp, intro=None)  # перемотка: рассказ забыт, вопрос «опубликовать?» остался в intro_post
        await s.commit()
    await tell_about_me(db, ctx, text="Я Тест, люблю шахматы и работаю на экструдере.", photo=False)
    await press_dialog(db, ctx, ask, "ch:pub:-2")
    assert not api.to(-2), "недописанный рассказ (без фото) не публикуется по старой кнопке"


async def test_double_tap_publish_keeps_result_line(db, ctx, api):
    await intro_setup(db)
    await tell_about_me(db, ctx)
    ask = api.to(500)[-1]
    await press_dialog(db, ctx, ask, "ch:pub:-2")
    done = api.answers[-1]["message"]["text"]
    assert "Готово, опубликовал в чате «Команда GTP»" in done
    await press_dialog(db, ctx, ask, "ch:pub:-2")  # второе нажатие со старым снимком текста
    assert len(api.to(-2)) == 1
    assert api.answers[-1]["message"] is None and api.answers[-1]["notification"] == T.POST_DONE, \
        "сообщение не затирается старым текстом"


# --- 4. чат из вопроса пропал или больше не «свой» -------------------------------------------------------

async def test_chat_gone_asks_again_about_new_chat(db, ctx, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx)
    ask = api.to(500)[-1]
    async with db() as s:  # пока новичок думал: бота удалили из «Команда GTP», общий чат отметили для представлений
        (await s.get(TeamChat, -2)).active = False
        (await s.get(TeamChat, -1)).for_intro = True
        await s.commit()
    await press_dialog(db, ctx, ask, "ch:pub:-2")
    assert not api.to(-1) and not api.to(-2), "без нового согласия ни в какой чат не публикуем"
    again = answered(api, ask)
    assert again["text"].startswith("Чат «Команда GTP» сейчас недоступен, туда не публикую.\n\n"
                                    "Хочешь, я сам представлю тебя коллегам в чате «Общий чат»?")
    assert [b["payload"] for b in api.buttons(again)] == ["ch:pub:-1", "ch:nopub"]
    post = (await emp_data(db, emp_id))["intro_post"]
    assert post["status"] == "asked" and post["chat_id"] == -1 and post["mid"] == ask["mid"]
    await press_dialog(db, ctx, again, "ch:pub:-1")
    assert len(api.to(-1)) == 1 and (await emp_data(db, emp_id))["intro_post"]["status"] == "published"


async def test_chat_no_longer_matches_is_not_used(db, ctx, api):
    emp_id = await intro_setup(db)
    await tell_about_me(db, ctx)
    ask = api.to(500)[-1]
    async with db() as s:  # HR поменял аудиторию: чат теперь для GrPP
        (await s.get(TeamChat, -2)).sites = ["GrPP"]
        await s.commit()
    await press_dialog(db, ctx, ask, "ch:pub:-2")
    assert not api.to(-2)
    assert (await emp_data(db, emp_id))["intro_post"]["status"] == "failed", "другого чата нет: не опубликовал"
    assert api.answers[-1]["message"]["text"].endswith(T.PUBLISH_FAILED[0])


# --- 5. ошибки addMembers ----------------------------------------------------------------------------

async def test_not_admin_is_retried_not_final(db, sender, api, monkeypatch):
    async with db() as s:
        s.add(make_chat(-1, title="Общий чат", link=None))  # боту без прав ссылку не видно
        await s.commit()
    api.member_result = {-1: MaxApiError(403, "chat.denied", "not admin")}
    (emp_id,) = await add(db, make_employee())
    await service.tick(DAY1, sender=sender, session_factory=db)
    entry = (await emp_data(db, emp_id))["chats"]["-1"]
    assert entry["status"] != "failed", "права чата — не окончательный отказ"
    async with db() as s:
        (card,) = await service.card(s, await s.get(Employee, emp_id))
    assert card["reason"] == T.ERROR_OTHER, "HR видит, что бот не админ"
    # HR сделал бота админом: следующая попытка добавляет
    api.member_result = {}
    later = datetime.now(UTC) + service.RETRY_AFTER + timedelta(minutes=1)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert (await emp_data(db, emp_id))["chats"]["-1"]["status"] == "added"
    assert len(api.members) == 2


async def test_retry_waits_between_attempts(db, sender, api):
    async with db() as s:
        s.add(make_chat(-1, title="Общий чат", link=None))
        await s.commit()
    api.member_result = {-1: MaxApiError(403, "chat.denied", "not admin")}
    await add(db, make_employee())
    await service.tick(DAY1, sender=sender, session_factory=db)
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert len(api.members) == 1, "не долбим addMembers каждые 5 минут"


async def test_http_privacy_code_is_recognised(db, sender, api):
    async with db() as s:
        s.add(make_chat(-1, title="Общий чат"))
        await s.commit()
    api.member_result = {-1: MaxApiError(400, service.PRIVACY, "privacy")}
    (emp_id,) = await add(db, make_employee())
    await service.tick(DAY1, sender=sender, session_factory=db)
    entry = (await emp_data(db, emp_id))["chats"]["-1"]
    assert entry["error"] == service.PRIVACY and entry["status"] == "link"
    assert "так настроена приватность в «МАКС»" in api.to(500)[-1]["text"]
    async with db() as s:
        (card,) = await service.card(s, await s.get(Employee, emp_id))
    assert card["reason"] == T.ERRORS[service.PRIVACY]


# --- 6. сбой вопроса о публикации не откатывает добавления ---------------------------------------------

async def test_offer_failure_keeps_added_chats(db, sender, api):
    emp_id = await intro_setup(db, make_chat(-2, title="Команда GTP", audience="sites", sites=["GTP"]), hr=None)
    async with db() as s:  # рассказ без фото, ожидание истекло: задача спросит про публикацию
        emp = await s.get(Employee, emp_id)
        engine._set_data(emp, intro={"text": INTRO, "photo_url": None, "at": "2026-11-02T10:00:00+00:00"})
        await s.commit()
    real_send = api.send_message

    async def send(text, *, chat_id=None, **kw):
        if chat_id == 500:
            raise MaxApiError(400, "bad.request", "boom")
        return await real_send(text, chat_id=chat_id, **kw)

    api.send_message = send
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert (await emp_data(db, emp_id))["chats"]["-2"]["status"] == "added", "добавление записано"
    await service.tick(DAY1, sender=sender, session_factory=db)
    assert len(api.members) == 1, "addMembers не повторяется"


# --- 7. название площадки в посте -----------------------------------------------------------------------

async def test_post_uses_site_name(db, ctx, api):
    await intro_setup(db, make_chat(-2, title="Офис", audience="sites", sites=["Luzhniki"]), hr=None,
                      site_code="Luzhniki", work_type="office", dialog_data={"onboarded": True, "position": "Менеджер"})
    async with db() as s:
        (await s.get(Site, "Luzhniki")).name = "Офис «Лужники»"
        await s.commit()
    await tell_about_me(db, ctx)
    await press_dialog(db, ctx, api.to(500)[-1], "ch:pub:-2")
    (post,) = api.to(-2)
    assert post["text"].startswith("Знакомьтесь, это Тест Тестов! Менеджер, офис «Лужники».\n\n")


async def test_site_label():
    assert service.site_label("Площадка GTP", "GTP") == "площадка GTP"
    assert service.site_label("Офис «Лужники»", "Luzhniki") == "офис «Лужники»"
    assert service.site_label("Группа ПОЛИПЛАСТИК (GrPP)", "GrPP") == "Группа ПОЛИПЛАСТИК"
    assert service.site_label(None, "GTP") == "площадка GTP"
