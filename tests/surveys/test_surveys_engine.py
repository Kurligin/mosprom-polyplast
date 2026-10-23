"""Опрос в чате: одно сообщение меняется на месте, текстовые ответы, флаги, обращения, напоминания."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import Flag, SurveyAnswer, Ticket
from app.surveys import engine
from app.surveys import texts as T

pytestmark = pytest.mark.asyncio


async def _count(s, model, *where) -> int:
    return await s.scalar(select(func.count()).select_from(model).where(*where))


async def test_day3_one_message_changes_in_place(world):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    assert mid and world.api.sent() == [mid]
    assert world.api.text(mid) == ("Первые пару дней на новом месте могут быть немного сложными, поэтому мы "
                                   "предлагаем пройти тебе опрос, чтобы поделиться с нами своими впечатлениями!"
                                   "\n\nВопрос 1 из 3\nБыли ли вопросы при оформлении?")
    assert world.api.rows(mid) == [["Все отлично!"], ["Остались вопросы"], ["Возникли трудности с документами"]]

    await world.press("Все отлично!", mid)
    assert "Как тебя встретили новые коллеги?" in world.api.text(mid)

    await world.press("Никак, все заняты", mid)
    assert world.api.text(mid).startswith("Жалко, что не удалось познакомиться")
    assert world.api.rows(mid) == [["Передать проблему в HR"], ["Связаться с HR"], ["Дальше"]]

    await world.press("Дальше", mid)
    assert "Понятны ли тебе задачи на испытательный срок?" in world.api.text(mid)

    await world.press("Да, все понятно!", mid)
    assert world.api.text(mid) == "Отлично 💪🏻\n\nСпасибо за обратную связь!\nУвидимся завтра 👋🏻"
    assert world.api.buttons(mid) == []

    assert world.api.sent() == [mid], "весь опрос — одно сообщение"
    assert await _count(world.session, SurveyAnswer, SurveyAnswer.employee_id == world.emp.id) == 3
    assert engine.pending_surveys(world.emp) == []
    assert world.emp.dialog_data["surveys"]["day3"]["done_at"]


async def test_start_is_idempotent(world):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    assert await engine.start_survey(world.repo, world.emp, "day3") == mid
    assert len(world.api.sent()) == 1


async def test_scale_two_rows_and_reason_below_8(world):
    mid = await engine.start_survey(world.repo, world.emp, "week2")
    assert world.api.text(mid).startswith("Привет, Анна!\nХотел узнать, как у тебя дела?")
    await world.press("Да", mid)
    await world.press("Да", mid)
    text = world.api.text(mid)
    assert "Вопрос 3 из 9" in text and "(1 - недоступен, 10 - всегда доступен)" in text
    assert world.api.rows(mid) == [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"]]

    await world.press("5", mid)
    assert world.api.text(mid).endswith("✅ 5\n\nЧто мешает? Напиши парой слов.")
    assert world.emp.dialog_state == "sv:week2:2:reason"

    await world.say("Руководитель всё время на совещаниях, отвечает через день")
    assert world.api.buttons(mid) == [], "у старого сообщения кнопки убраны, отметка выбора осталась"
    assert "✅ 5" in world.api.text(mid)
    new = world.api.last()
    assert new != mid and world.api.text(new).startswith("Спасибо, записал.\n\nВопрос 4 из 9")
    assert world.emp.dialog_state is None
    ans = (await world.session.scalars(select(SurveyAnswer).where(SurveyAnswer.score == 5, SurveyAnswer.employee_id == world.emp.id))).one()
    assert ans.text.startswith("Руководитель всё время") and ans.mood == "neutral"

    await world.press("9", new)
    assert "Вопрос 5 из 9" in world.api.text(new), "8 и выше — без вопроса о причине"


async def test_red_flag_in_reason_text(world):
    mid = await engine.start_survey(world.repo, world.emp, "week2")
    await world.press("Нет", mid)
    await world.say("Мастер орёт и унижает при всех, а к станку поставили без инструктажа")
    flags = (await world.session.scalars(select(Flag).where(Flag.employee_id == world.emp.id))).all()
    assert {f.topic for f in flags} == {"травля", "безопасность"} and {f.level for f in flags} == {"red"}
    assert all(f.ticket_id for f in flags)
    tickets = (await world.session.scalars(select(Ticket).where(Ticket.source == "flag",
                                                               Ticket.employee_id == world.emp.id))).all()
    assert len(tickets) == 1, "один текст — одно обращение, оба флага на нём"
    assert {f.ticket_id for f in flags} == {tickets[0].id} and tickets[0].category == "safety"
    notice = world.api.text(world.api.last(500))
    assert notice.startswith("Это важно. Я сразу передал твоё сообщение HR, его увидит только HR.")
    assert "Вопрос 2 из 9" in notice
    group = [world.api.text(m) for m in world.api.sent(900)]
    assert any("Красный флаг" in g for g in group), "уведомление в HR-группу"


async def test_multi_exclusive_and_yellow_flag(world):
    world.emp.dialog_data = {**world.emp.dialog_data}
    mid = await engine.start_survey(world.repo, world.emp, "month2")
    await world.press("С отличным! Мне все нравится!", mid)
    await world.press("Руководитель", mid)
    await world.press("Коллеги", mid)
    assert ["✓ Руководитель"] in world.api.rows(mid) and ["✓ Коллеги"] in world.api.rows(mid)
    await world.press("Никто!", mid)
    rows = world.api.rows(mid)
    assert ["✓ Никто!"] in rows and ["Руководитель"] in rows and ["Коллеги"] in rows
    await world.press("Готово", mid)
    assert world.api.text(mid).startswith("Поддержка - важная составляющая успешной адаптации.")
    flag = (await world.session.scalars(select(Flag).where(Flag.employee_id == world.emp.id))).one()
    assert (flag.level, flag.topic, flag.source, flag.ticket_id) == ("yellow", "наставник", "button", None)
    await world.press("Дальше", mid)
    assert "Удаленный формат работы" in [r[0] for r in world.api.rows(mid)], "вариант для GrPP"

    await world.press("Свой вариант", mid)
    assert world.api.text(mid).endswith("✅ Свой вариант\n\nНапиши свой вариант.")
    await world.say("Наставник на первые две недели")
    assert world.api.text(world.api.last()) == "Спасибо за обратную связь!"
    ans = (await world.session.scalars(select(SurveyAnswer).where(SurveyAnswer.value == "Свой вариант",
                                                              SurveyAnswer.employee_id == world.emp.id))).one()
    assert ans.text == "Наставник на первые две недели"


async def test_gtp_has_no_remote_option(world):
    world.emp.site_code = "GTP"
    mid = await engine.start_survey(world.repo, world.emp, "month2")
    await world.press("С отличным! Мне все нравится!", mid)
    await world.press("Коллеги", mid)
    await world.press("Готово", mid)
    labels = [r[0] for r in world.api.rows(mid)]
    assert "Удаленный формат работы" not in labels and labels[0] == "Более четкие задачи"


async def test_contact_recruiter_creates_ticket(world):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    await world.press("Остались вопросы", mid)
    assert world.api.rows(mid) == [["Связаться с рекрутером"], ["Дальше"]]
    await world.press("Связаться с рекрутером", mid)
    t = (await world.session.scalars(select(Ticket).where(Ticket.employee_id == world.emp.id))).one()
    assert t.source == "survey" and t.category == "contact_request" and t.text.startswith("Просит связаться")
    assert t.assignee_id == world.rec.id
    assert world.api.text(mid).startswith("Передал рекрутеру, он свяжется с тобой.\n\nВопрос 2 из 3")


async def test_forward_problem_text_to_ticket(world):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    await world.press("Возникли трудности с документами", mid)
    assert world.api.text(mid).endswith("В чем заключалась проблема?")
    assert world.emp.dialog_state == "sv:day3:0:problem"
    await world.say("Не дали копию трудового договора")
    t = (await world.session.scalars(select(Ticket).where(Ticket.employee_id == world.emp.id))).one()
    assert t.text.startswith("Не дали копию трудового договора") and "Опрос по итогам третьего" in t.text
    assert world.api.text(world.api.last()).startswith("Передал в отдел подбора. Ответ придёт сюда.\n\nВопрос 2 из 3")


async def test_intent_to_leave_red_flag_without_notice(world):
    world.emp.dialog_data = {**world.emp.dialog_data}
    mid = await engine.start_survey(world.repo, world.emp, "final")
    await world.press("Со всем справился", mid)
    await world.press("Нет, задумываюсь об уходе", mid)
    assert "✅ Нет, задумываюсь об уходе\n\nОчень жаль, что ты сомневаешься" in world.api.text(mid)
    assert ["Готов озвучить причину в личной коммуникации с рекрутером"] in world.api.rows(mid)
    flag = (await world.session.scalars(select(Flag).where(Flag.employee_id == world.emp.id))).one()
    assert (flag.level, flag.topic) == ("red", "намерение уйти") and flag.ticket_id
    await world.say("Далеко ездить, два часа в одну сторону")
    out = world.api.text(world.api.last())
    assert out.startswith("Спасибо, записал.") and "увидит только HR" not in out


async def test_access_no_reminds_manager(world, monkeypatch):
    from app.manager import service as manager

    calls = []

    async def fake_remind(repo, emp):
        calls.append(emp.id)
        return True

    monkeypatch.setattr(manager, "remind_access", fake_remind)
    mid = await engine.start_survey(world.repo, world.emp, "day1")
    assert "Есть ли у тебя все необходимые доступы и материалы?" in world.api.text(mid)
    assert world.api.rows(mid) == [["Да, все есть", "Нет"]]
    await world.press("Нет", mid)
    assert world.api.rows(mid) == [["Напомнить руководителю про доступы"]]
    assert engine.pending_surveys(world.emp) == [], "ответ дан — напоминать об опросе не нужно"
    await world.press("Напомнить руководителю про доступы", mid)
    assert calls == [world.emp.id]
    assert world.api.text(mid).endswith("Напомнил руководителю про доступы.")
    assert world.api.text(mid).startswith("Ничего страшного!")


async def test_access_question_for_gtp(world):
    world.emp.site_code = "GTP"
    mid = await engine.start_survey(world.repo, world.emp, "day1")
    assert "Все ли необходимое у вас в наличии?" in world.api.text(mid)


async def test_reminders_next_day_and_day_after(world):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    st = world.emp.dialog_data["survey"]
    started = datetime.fromisoformat(st["started_at"])
    day1 = started.replace(hour=8, minute=0) + timedelta(days=1)   # 11:00 МСК
    assert engine.reminder_due(world.emp, st, "Europe/Moscow", started + timedelta(hours=1)) == 0
    assert engine.reminder_due(world.emp, st, "Europe/Moscow", day1.replace(hour=5)) == 0, "до 10:00 не пишем"
    assert await engine.remind_all(world.session, day1, [world.emp.id]) == 1
    rem = world.api.last()
    assert world.api.text(rem).startswith("Привет, Анна!\nПишу с небольшим напоминанием, что ты не прошел опрос.")
    assert world.api.rows(rem) == [["Продолжить опрос"]]
    assert await engine.remind_all(world.session, day1 + timedelta(hours=3), [world.emp.id]) == 0
    assert await engine.remind_all(world.session, day1 + timedelta(days=1), [world.emp.id]) == 1
    assert await engine.remind_all(world.session, day1 + timedelta(days=5), [world.emp.id]) == 0

    await world.press("Продолжить опрос", rem)
    assert world.api.text(mid) == T.MOVED_DOWN and world.api.buttons(mid) == []
    assert world.api.text(rem).endswith("Были ли вопросы при оформлении?")
    await world.press("Все отлично!", rem)
    assert "Как тебя встретили новые коллеги?" in world.api.text(rem)


async def test_finished_survey_no_reminder(world):
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert world.api.text(mid) == "Прекрасно! Тогда, продолжай в том же духе 💪🏻"
    later = datetime.now(UTC) + timedelta(days=1, hours=12)
    assert await engine.remind_all(world.session, later, [world.emp.id]) == 0


async def test_new_survey_closes_unfinished(world):
    old = await engine.start_survey(world.repo, world.emp, "day3")
    new = await engine.start_survey(world.repo, world.emp, "week2")
    assert old != new and world.api.buttons(old) == []
    await world.press("Все отлично!", old) if world.api.buttons(old) else None
    assert world.emp.dialog_data["survey"]["code"] == "week2"


async def test_stale_text_wait_goes_to_free_text(world):
    mid = await engine.start_survey(world.repo, world.emp, "week2")
    await world.press("Нет", mid)
    st = dict(world.emp.dialog_data["survey"])
    st["wait_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    world.emp.dialog_data = {**world.emp.dialog_data, "survey": st}
    await world.say("Когда зарплата?")
    assert world.emp.dialog_state is None
    assert world.emp.dialog_data.get("last_question") == "Когда зарплата?", "ушло в обычный маршрут вопроса"


async def test_skip_text(world):
    mid = await engine.start_survey(world.repo, world.emp, "week2")
    await world.press("Нет", mid)
    await world.press("Пропустить", mid)
    assert world.emp.dialog_state is None and "Вопрос 2 из 9" in world.api.text(mid)


async def test_old_button_toast(world):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    first = world.api.buttons(mid)
    await world.press("Все отлично!", mid)
    # нажатие той же кнопки из старой клавиатуры
    world.api.msgs[mid]["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": [[
        {"type": "callback", "text": first[0][0], "payload": first[0][1]}]]}}]
    await world.press("Все отлично!", mid)
    assert world.api.toasts[-1] == T.STALE


async def test_finished_survey_not_restarted_except_test_account(world):
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert await engine.start_survey(world.repo, world.emp, "plan") is None
    world.emp.is_test = True  # машина времени перемотала назад — тестовый новичок проходит заново
    again = await engine.start_survey(world.repo, world.emp, "plan")
    assert again and again != mid
    assert "done_at" not in engine.survey_history(world.emp)["plan"]


async def test_survey_image_stays_on_card(world, monkeypatch):
    from app.bot import uploads

    async def fake_attachment(path, repo=None, sender=None):
        return {"type": "image", "payload": {"token": f"tok-{path.name}"}}

    monkeypatch.setattr(uploads, "attachment", fake_attachment)
    mid = await engine.start_survey(world.repo, world.emp, "week2")
    images = lambda m: [a for a in world.api.msgs[m]["attachments"] if a["type"] == "image"]
    assert images(mid) == [{"type": "image", "payload": {"token": "tok-img_14.png"}}]
    await world.press("Да", mid)
    assert images(mid) and "Вопрос 2 из 9" in world.api.text(mid), "картинка остаётся при правке"
    await world.press("Нет", mid)
    await world.say("Не понимаю, где брать регламенты")
    assert images(mid) and world.api.buttons(mid) == []
    assert images(world.api.last()) == [], "в новом сообщении картинки нет"
