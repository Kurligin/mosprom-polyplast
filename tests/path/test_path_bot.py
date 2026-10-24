"""«Путь новичка» в боте: карточка и строка о вехе в уже существующих сообщениях, мерч-сюрприз для HR.

Фейковый «МАКС» и настоящая БД в откатываемой транзакции — как у опросов (tests/surveys/fakes.py).
Отрисовка карточки подменена (Chromium здесь не нужен), настоящая — в test_real_render_png.
"""

from __future__ import annotations

import pytest

from app.bot import handlers
from app.path import card
from app.path import service as P
from app.path import texts as T
from app.surveys import engine

pytestmark = pytest.mark.asyncio

HR_GROUP = 900


@pytest.fixture
def fake_card(world, monkeypatch, path_on):
    """Карточка без Chromium: байты зависят от состояния пути. Загрузка в фейковый «МАКС» — по токену."""
    rendered = []

    async def render_png(state, name=None):
        rendered.append(state)
        return f"png:{state.passed}:{state.current}".encode()

    async def upload(kind, filename, data, content_type):
        token = f"tok-{data.decode()}" if filename == card.FILE_NAME else f"tok-{filename}"
        return {"photos": {"p": {"token": token}}}

    monkeypatch.setattr(card, "render_png", render_png)
    world.api.upload = upload
    return rendered


def images(world, mid):
    return [a for a in world.api.msgs[mid]["attachments"] if a.get("type") == "image"]


def cards(world, mid):
    """Карточки пути в сообщении (картинка шага опроса — не карточка)."""
    return [a for a in images(world, mid) if a["payload"]["token"].startswith("tok-png")]


def hr_sends(world):
    return [m for m in world.api.sent(HR_GROUP)]


async def milestone(world, code):
    """Как опрос: строка о вехе, «спасибо» новичку, потом карточка правкой и (на последней вехе) HR-группа."""
    line = P.line_for(world.emp, code)
    (mid,) = await world.ctx.sender.send(world.repo, f"Спасибо!\n\n{line}", chat_id=500)
    att = await P.after_shown(world.repo, world.emp, code, sender=world.ctx.sender, mid=mid,
                              text=f"Спасибо!\n\n{line}", atts=[], chat_id=500)
    return line, att


# --- «спасибо» опроса --------------------------------------------------------------------------

async def test_survey_thanks_gets_line_and_card_in_same_message(world, fake_card):
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    for label in ("Все отлично!", "Отлично!", "Да, все понятно!"):
        await world.press(label, mid)
    text = world.api.text(mid)
    assert text.startswith("Отлично 💪🏻\n\nСпасибо за обратную связь!"), "тексты опроса из ТЗ не меняются"
    assert text.endswith("Веха 3 из 7 пройдена. Дальше: «Две недели».")
    assert images(world, mid) == [{"type": "image", "payload": {"token": "tok-png:2:2"}}]
    assert world.api.sent(500) == [mid], "новых сообщений новичку нет: правка на месте"
    assert world.emp.dialog_data["surveys"]["day3"]["done_at"]


async def test_line_is_the_same_for_good_and_bad_answers(world, fake_card):
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    good = world.api.text(mid).rsplit("\n\n", 1)[-1]

    engine.reset(world.emp, "plan")
    mid2 = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Я отстаю от графика.", mid2)
    bad = world.api.text(mid2).rsplit("\n\n", 1)[-1]

    assert good == bad == "Веха 5 из 7 пройдена. Дальше: «Два месяца»."
    assert cards(world, mid) and cards(world, mid2), "карточка при любых ответах"
    assert [b for b, _ in world.api.buttons(mid2)] == ["Передать проблему в подбор", "Связаться с рекрутером"]


async def test_card_moves_to_new_message_after_text_answer(world, fake_card):
    """Последний ответ текстом: «спасибо» и так приходит новым сообщением, карточка — в нём, а не отдельно."""
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Я отстаю от графика.", mid)
    await world.press("Передать проблему в подбор", mid)
    assert not cards(world, mid) and "Веха" not in world.api.text(mid), "пока ждём текст, вехи нет"
    sent_before = len(world.api.sent(500))
    await world.say("Не успеваю пройти обучение на линии")
    last = world.api.last(500)
    assert len(world.api.sent(500)) == sent_before + 1
    assert world.api.text(last).endswith("Веха 5 из 7 пройдена. Дальше: «Два месяца».")
    assert cards(world, last) and not cards(world, mid), "карточка только в последнем сообщении"
    assert images(world, mid), "картинка шага остаётся в первом сообщении опроса"
    assert len(fake_card) == 1, "карточка нарисована один раз"


async def test_done_button_comes_first_then_milestone(world, fake_card):
    """Последний вопрос с «Готово»: строка о вехе появляется после «Готово», вместе со «спасибо»."""
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    await world.press("Все отлично!", mid)
    await world.press("Отлично!", mid)
    await world.press("Жду разъяснений от руководителя", mid)
    assert "Веха" not in world.api.text(mid) and not images(world, mid)
    await world.press("Готово", mid)
    assert world.api.text(mid).endswith("Веха 3 из 7 пройдена. Дальше: «Две недели».")
    assert images(world, mid)


async def test_survey_image_replaced_by_card_only_in_thanks(world, fake_card, monkeypatch):
    from app.bot import uploads

    async def fake_attachment(path, repo=None, sender=None):
        return {"type": "image", "payload": {"token": f"step-{path.name}"}}

    monkeypatch.setattr(uploads, "attachment", fake_attachment)
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    assert images(world, mid) == [{"type": "image", "payload": {"token": "step-img_07.png"}}]
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert [a["payload"]["token"] for a in images(world, mid)] == ["tok-png:2:4"]


async def test_no_chromium_no_card_and_no_crash(world, monkeypatch, path_on):
    async def broken(state, name=None):
        raise RuntimeError("Executable doesn't exist: chromium")

    monkeypatch.setattr(card, "render_png", broken)
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert world.api.text(mid).endswith("Веха 5 из 7 пройдена. Дальше: «Два месяца».")
    assert not cards(world, mid)
    assert world.emp.dialog_data["surveys"]["plan"]["done_at"]


async def test_flag_off_changes_nothing(world, fake_card, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "path_enabled", False)
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert world.api.text(mid) == "Прекрасно! Тогда, продолжай в том же духе 💪🏻"
    assert not cards(world, mid) and not fake_card


@pytest.mark.parametrize("field, value", [("is_synthetic", True), ("status", "stopped")])
async def test_synthetic_and_stopped_get_nothing(world, fake_card, field, value):
    for c in ("day1", "day3", "week2", "plan", "month2"):
        world.emp.dialog_data = {**world.emp.dialog_data,
                                 "surveys": {**(world.emp.dialog_data.get("surveys") or {}),
                                             c: {"started_at": "x", "done_at": "x"}}}
    setattr(world.emp, field, value)
    line, att = await milestone(world, "month2")
    assert (line, att) == (None, None) and not fake_card
    world.emp.dialog_data = {**world.emp.dialog_data, "surveys": {
        **world.emp.dialog_data["surveys"], "final": {"started_at": "x", "done_at": "x"}}}
    assert await milestone(world, "final") == (None, None)
    assert not hr_sends(world)


@pytest.mark.parametrize("enabled", [True, False])
async def test_next_question_mid_saved_after_text_answer(world, fake_card, monkeypatch, enabled):
    """Регрессия: после текстового ответа mid нового сообщения опроса доходит до БД (отправка делает flush)."""
    from app.config import settings

    monkeypatch.setattr(settings, "path_enabled", enabled)
    mid = await engine.start_survey(world.repo, world.emp, "week2")
    await world.press("Да", mid)
    await world.press("Нет", mid)
    await world.say("Не хватает инструкции по переналадке")
    await world.session.refresh(world.emp)
    assert world.emp.dialog_data["survey"]["mid"] == world.api.last(500) != mid


# --- HR-группа: мерч-сюрприз -----------------------------------------------------------------------

def _all_done(emp, *, final: bool) -> None:
    codes = ["day1", "day3", "week2", "plan", "month2"] + (["final"] if final else [])
    emp.dialog_data = {**emp.dialog_data, "surveys": {c: {"started_at": "x", "done_at": "x"} for c in codes}}


async def test_hr_group_gets_merch_once_only_after_last_milestone(world, fake_card):
    _all_done(world.emp, final=False)
    line, _ = await milestone(world, "month2")
    assert line == "Веха 6 из 7 пройдена. Дальше: «Конец испытательного срока»."
    assert not hr_sends(world), "до последней вехи HR-группа ничего не получает"

    _all_done(world.emp, final=True)
    line, att = await milestone(world, "final")
    assert line == "Веха 7 из 7 пройдена. Весь путь адаптации позади!" and att
    (sent,) = hr_sends(world)
    text = world.api.text(sent)
    from app.chats.service import site_name

    assert text == T.HR_FINISHED.format(name="Анна Опросова", site=await site_name(world.session, world.emp))
    assert text.startswith("Путь адаптации пройден: Анна Опросова, ")
    assert "термокружк" in text and "₽" not in text
    assert world.emp.dialog_data["path"]["hr_finished_at"]

    # повтор (двойной показ «спасибо», перемотка и повторное прохождение) — второго сообщения нет
    await milestone(world, "final")
    assert len(hr_sends(world)) == 1


async def test_newcomer_is_not_told_about_gift(world, fake_card):
    _all_done(world.emp, final=True)
    line, _ = await milestone(world, "final")
    newcomer = " ".join([line or "", *T.LABELS.values(), T.HELLO, T.LINE, T.LINE_LAST, T.CARD_DONE])
    for word in ("подар", "мерч", "сюрприз", "приз", "наград"):
        assert word not in newcomer.lower()


async def test_hr_group_not_connected_no_crash(world, fake_card):
    from app.db.models import Setting

    await world.session.delete(await world.session.get(Setting, "hr_group_chat_id"))
    await world.session.flush()
    _all_done(world.emp, final=True)
    line, _ = await milestone(world, "final")
    assert line and not hr_sends(world)
    assert "hr_finished_at" not in (world.emp.dialog_data.get("path") or {})


# --- знакомство и /start --------------------------------------------------------------------------

async def test_onboarding_final_message_gets_first_milestone(world, fake_card):
    emp = world.emp
    emp.status, emp.dialog_data, emp.dialog_state = "waiting", {}, handlers.S_AI
    await handlers.start_onboarding(world.ctx, world.repo, emp)
    mid = world.api.last(500)
    await world.press("Можно", mid)
    text = world.api.text(mid)
    assert text.startswith("Очень приятно с тобой познакомиться, Анна!")
    assert text.endswith("Веха 1 из 7 пройдена. Дальше: «Первый день».")
    assert images(world, mid) == [{"type": "image", "payload": {"token": "tok-png:1:0"}}]
    assert world.api.sent(500) == [mid], "знакомство остаётся одним сообщением"


async def test_hello_again_shows_progress_in_same_message(world, fake_card):
    world.emp.dialog_data = {**world.emp.dialog_data,
                             "surveys": {"day1": {"started_at": "x", "done_at": "x"}}}
    await handlers.handle_update(world.ctx, world.repo,
                                 {"update_type": "bot_started", "chat_id": 500, "payload": "",
                                  "user": {"user_id": 100, "name": "Анна"}})
    assert world.api.text(world.api.last(500)) == (
        "Я на связи. Если есть вопрос, просто напиши его.\n\nПройдено 2 из 7 вех.")
    assert not fake_card, "в приветствии только строка, без картинки"


async def test_action_button_after_thanks_keeps_one_line_one_render(world, fake_card):
    """«Спасибо» показан с кнопкой-действием (plan, плохой ответ). Нажатие «Связаться с рекрутером» меняет то же
    сообщение: строка о вехе одна, карточка остаётся, второй раз не рисуется."""
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Я отстаю от графика.", mid)
    assert cards(world, mid) and len(fake_card) == 1
    await world.press("Связаться с рекрутером", mid)
    text = world.api.text(mid)
    assert text.count("Веха 5 из 7") == 1 and text.endswith("Веха 5 из 7 пройдена. Дальше: «Два месяца».")
    assert "Передал рекрутеру" in text
    assert cards(world, mid) and len(fake_card) == 1
    assert world.api.sent(500) == [mid]


async def test_card_not_ready_retried_then_attached(world, fake_card):
    """Свежая картинка у «МАКС» «ещё не готова»: правку повторяем, карточка встаёт."""
    from app.bot.api import MaxApiError

    real_edit, calls = world.api.edit_message, []

    async def edit(mid, text, *, attachments=None, fmt=None):
        calls.append(mid)
        if len(calls) < 3:
            raise MaxApiError(400, "attachment.not.ready", "Key: errors.process.attachment.file.not.processed")
        return await real_edit(mid, text, attachments=attachments, fmt=fmt)

    world.api.edit_message = edit
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert len(calls) == 3 and cards(world, mid)
    assert world.api.text(mid).endswith("Веха 5 из 7 пройдена. Дальше: «Два месяца».")


async def test_card_never_ready_message_stays_with_line(world, fake_card):
    from app.bot.api import MaxApiError

    async def edit(mid, text, *, attachments=None, fmt=None):
        raise MaxApiError(400, "attachment.not.ready", "not processed")

    world.api.edit_message = edit
    mid = await engine.start_survey(world.repo, world.emp, "plan")
    await world.press("Все хорошо! Продвигаюсь по плану", mid)
    assert world.api.text(mid).endswith("Веха 5 из 7 пройдена. Дальше: «Два месяца».")
    assert not cards(world, mid) and world.api.buttons(mid) == []
    assert world.emp.dialog_data["survey"]["path"]["att"] is None


async def test_upload_error_no_card_no_crash(world, fake_card):
    from app.bot.api import MaxApiError

    async def upload(kind, filename, data, content_type):
        raise MaxApiError(400, "upload.failed", "boom")

    world.api.upload = upload
    mid = await engine.start_survey(world.repo, world.emp, "day3")
    for label in ("Все отлично!", "Отлично!", "Да, все понятно!"):
        await world.press(label, mid)
    assert world.api.text(mid).endswith("Веха 3 из 7 пройдена. Дальше: «Две недели».")
    assert not cards(world, mid)


async def test_hr_group_hears_after_newcomer_sees_thanks(world, fake_card):
    _all_done(world.emp, final=True)
    await milestone(world, "final")
    order = [mid for act, mid in world.api.log if act == "send"]
    assert world.api.msgs[order[0]]["chat_id"] == 500 and world.api.msgs[order[-1]]["chat_id"] == HR_GROUP


# --- настоящая отрисовка ---------------------------------------------------------------------------

async def test_real_render_png():
    """Chromium из образа: PNG 1080 × 1000. Нет браузера — пропуск (в боте тогда просто без картинки)."""
    import struct
    from types import SimpleNamespace

    from app.reports import render

    emp = SimpleNamespace(site_code="GTP", status="active", is_synthetic=False, first_name="Иван",
                          dialog_data={"onboarded": True, "surveys": {"day1": {"started_at": "x", "done_at": "x"}}})
    state = P.path_state(emp)
    html = card.render_html(state, "Иван")
    assert "Пройдено 2 из 7" in html and "Дальше: «Третий день»" in html and "вы здесь" in html
    try:
        png = await card.render_png(state, "Иван")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium недоступен: {e}")
    finally:
        await render.close()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (card.WIDTH, card.HEIGHT)
