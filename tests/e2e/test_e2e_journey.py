"""Сквозной тест «путь сотрудника»: оператор производства на площадке GTP от приглашения до конца испытательного.

Прогон один на модуль (фикстура `journey` в conftest.py, сценарий — tests/e2e/journey.py), тесты проверяют
его по этапам: нет исключений, бот не флудит, статусы отправок, ответы опросов, флаги, обращения,
встречи руководителя, руководитель не видит ответов и обращений, в своих фразах бота нет «—»,
HR-группа выбрана кнопкой, новичок в чатах команды, представление коллегам только с согласия.
Маршрут для людей — eval/journey/gtp-operator.md (пишется при каждом прогоне).
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from app.scenario import engine
from tests.e2e import journey as J

# Бот не флудит: сколько НОВЫХ сообщений получатель может получить за этап (правки на месте не считаются).
# (новичку, HR-группе, руководителю, каждому чату команды)
LIMITS = {
    "onboarding": (3, 0, 0, 0),     # знакомство одним сообщением + шаги «за 2–3 дня до выхода» (картинка и текст)
    "day0": (2, 0, 0, 0),           # «завтра первый день» с кнопками + напоминание про документы
    # 7 шагов первого дня + карта и файл «где пообедать» по кнопкам + одна ссылка на общий чат; push про доступы
    "day1": (10, 0, 1, 0),
    "day1-question": (1, 1, 0, 0),  # карточка ответа; обращение по кнопке «Связаться с HR»
    # просьба рассказать о себе, «пришли фото», «спасибо» с вопросом о публикации; рассказ в чате GTP
    "day2": (3, 1, 0, 1),
    "day3": (3, 0, 1, 0),           # шаг дня + опрос одним сообщением; push «проведи встречу»
    "day3-red": (1, 1, 0, 0),       # «передал HR, увидит только HR»; красный флаг в HR-группу
    "day3-hr": (2, 2, 0, 0),        # ответ HR + «Передал HR.»; в группе: правка статуса и «новый ответ»
    "day14": (3, 0, 0, 0),          # опрос + следующий вопрос после текстового ответа
    "live": (4, 0, 1, 0),           # дни без перемотки: шаги дня и опрос
    "day60": (2, 0, 1, 0),
    # поздравление, опрос (+2 после текстовых ответов), прощание; HR-группе — «путь пройден, можно вручить мерч»
    "end": (6, 1, 0, 0),
}


def limits(key: str) -> tuple[int, int, int, int]:
    return LIMITS["live" if key.startswith("live") else key]


def new_sends(run: J.Journey, st: J.Stage, who: J.Person) -> list[dict]:
    return [e for e in run.api.log[st.start:st.end] if e["op"] == "send" and e["chat_id"] == who.chat_id]


def edits(run: J.Journey, st: J.Stage, who: J.Person) -> list[dict]:
    return [e for e in run.api.log[st.start:st.end] if e["op"] in ("answer", "edit") and e["chat_id"] == who.chat_id]


def labels(e: dict) -> list[str]:
    return [b.get("text") for b in J.buttons(e["attachments"])]


def ok(run: J.Journey, key: str) -> J.Stage:
    st = run.stage_by(key)
    assert not run.errors(st), "\n".join(run.errors(st))
    return st


def mine(rows: dict, emp_id: int) -> dict:
    return {i: r for i, r in rows.items() if r.get("employee_id") == emp_id}


# --- весь путь ---------------------------------------------------------------------------------------

def test_route_covers_whole_probation(journey):
    keys = [s.key for s in journey.stages]
    for key in ("setup", "onboarding", "day0", "day1", "day1-question", "day2", "day3", "day3-red", "day3-hr",
                "day14", "day60", "end"):
        assert key in keys
    assert any(k.startswith("live") for k in keys), "между 14 и 60 днём нет «живых» дней"
    days = [s.day for s in journey.stages if s.key != "setup"]
    assert days == sorted(days), f"дни идут не по порядку: {days}"
    assert days[0] < 0 and journey.stage_by("end").vdate == J.PROBATION_END


def test_all_bot_modules_loaded(journey):
    from app import plugins

    assert journey.plugins == plugins.MODULES


def test_no_exceptions(journey):
    assert journey.errors() == []
    assert journey.warnings() == []
    assert journey.llm_calls == [], "в e2e LLM не нужен: ответ по базе знаний уверенный"


@pytest.mark.parametrize("who, idx", [(J.NEWCOMER, 0), (J.HR_GROUP, 1), (J.MANAGER, 2), (J.GENERAL_CHAT, 3),
                                      (J.GTP_CHAT, 3)])
def test_bot_does_not_flood(journey, who, idx):
    over = []
    for st in journey.stages:
        if st.key == "setup":
            continue
        got = len(new_sends(journey, st, who))
        if got > limits(st.key)[idx]:
            over.append(f"{st.key}: {got} > {limits(st.key)[idx]}")
    assert not over, f"{who.label}: слишком много сообщений: {over}"


def test_deliveries_resolved_and_for_gtp_production(journey):
    """После каждого этапа нет «planned» и «failed»; новичок GTP-производства не получает шаги других площадок."""
    for st in journey.stages:
        rows = mine(st.db_after.get("deliveries", {}), journey.emp_id)
        bad = {i: r for i, r in rows.items() if r["status"] not in ("sent", "skipped")}
        assert not bad, f"{st.key}: {bad}"
    rows = mine(journey.stage_by("end").db_after["deliveries"], journey.emp_id)
    for r in rows.values():
        assert not r["sites"] or "GTP" in r["sites"], r
        if r["status"] == "sent" and r["error"] != engine.COVERED_NOTE:
            assert not r["office_only"] or r["gate"] == "plan_ready", f"офисный шаг производству: {r}"
    sent = [r for r in rows.values() if r["status"] == "sent" and r["error"] != engine.COVERED_NOTE]
    assert all(r["mid"] or (r["error"] or "").startswith("в составе опроса") for r in sent)


def test_setup_hr_group_by_button_and_team_chats(journey):
    """HR-группа — кнопкой в самой группе, ответ на месте; в чатах команды — одно знакомство без кнопки."""
    st = ok(journey, "setup")
    (prompt,) = new_sends(journey, st, J.HR_GROUP)
    assert labels(prompt) == ["Сделать эту группу HR-группой"]
    done = edits(journey, st, J.HR_GROUP)
    assert [e["mid"] for e in done] == [prompt["mid"]] and done[0]["text"].startswith("Готово, теперь это HR-группа")
    assert not labels(done[0])
    for chat in J.TEAM_CHATS:
        (hello,) = new_sends(journey, st, chat)
        assert not labels(hello) and "первый рабочий день" in hello["text"]
    chats = st.db_after["team_chats"]
    assert chats[J.GENERAL_CHAT.chat_id]["audience"] == "all"
    assert (chats[J.GTP_CHAT.chat_id]["audience"], chats[J.GTP_CHAT.chat_id]["sites"]) == ("sites", ["GTP"])
    assert chats[J.HR_GROUP.chat_id]["audience"] == "none" and chats[J.HR_GROUP.chat_id]["title"] == "HR ПОЛИПЛАСТИК"


def test_day1_team_chats_added_and_one_link(journey):
    """Первый рабочий день: в чат GTP добавлен, в общий «МАКС» не пустил — одно сообщение со ссылкой."""
    st = ok(journey, "day1")
    members = [e for e in journey.api.log if e["op"] == "members"]
    assert sorted((e["chat_id"], e["text"]) for e in members) == [
        (J.GENERAL_CHAT.chat_id, J.PRIVACY), (J.GTP_CHAT.chat_id, "")], "по разу в каждый чат за весь путь"
    assert all(st.start <= journey.api.log.index(e) < st.end for e in members), "добавляем в первый рабочий день"
    links = [e for e in new_sends(journey, st, J.NEWCOMER) if any(b["type"] == "link" for b in J.buttons(e["attachments"]))]
    (msg,) = links
    assert msg["text"] == ("Вот чаты твоей команды. В чат «Команда GTP» я тебя уже добавил, а сюда сам не смог: "
                           "так настроена приватность в «МАКС». Вступи по кнопке ниже, там коллеги и все новости.")
    assert [(b["text"], b["url"]) for b in J.buttons(msg["attachments"])] == [
        (J.GENERAL_CHAT.full_name, J.CHAT_INFO[J.GENERAL_CHAT.chat_id]["link"])]
    every = [e for e in journey.api.log if e["op"] == "send" and e["chat_id"] == J.NEWCOMER.chat_id
             and any(b["type"] == "link" for b in J.buttons(e["attachments"]))]
    assert every == [msg], "ссылки — одно сообщение за весь путь"
    chats = journey.final["dialog_data"]["chats"]
    assert {k: v["status"] for k, v in chats.items()} == {str(J.GENERAL_CHAT.chat_id): "link",
                                                          str(J.GTP_CHAT.chat_id): "added"}
    assert not any(e["op"] == "members" for e in journey.api.log[:journey.stage_by("day1").start])


def test_staff_personal_chats_quiet(journey):
    """HR-группа подключена: обращения идут туда, в личку HR, рекрутеру и главе подбора — только приветствие."""
    for who in (J.HR, J.RECRUITER, J.HEAD):
        got = [e for e in journey.api.log if e["op"] == "send" and e["chat_id"] == who.chat_id]
        assert len(got) == 1 and got[0]["text"].startswith("Готово, вы подключены"), who.label


def test_manager_gets_only_meeting_pushes(journey):
    assert journey.manager_leaks() == []
    from app.bot import texts as BT
    from app.manager import texts as MT

    # только тексты модуля руководителя: push о встречах, «Встреча проведена», напоминание про доступы
    templates = [*MT.PUSH.values(), MT.PUSH_PROBATION, MT.DONE, MT.ACCESS, MT.ACCESS_NO_MANAGER,
                 BT.STAFF_BOUND + "\n\n" + BT.STAFF_HINT]
    allowed = [re.compile(re.sub(r"\\\{\w+\\\}", ".+", re.escape(t)), re.DOTALL) for t in templates]
    texts = [e["text"] for e in journey.bot_entries() if e["chat_id"] == J.MANAGER.chat_id]
    assert texts and all(any(rx.fullmatch(t) for rx in allowed) for t in texts), texts


def test_no_long_dash_in_bot_phrases(journey):
    assert journey.dash_violations() == []


def test_route_written(journey):
    md = J.MD_PATH.read_text(encoding="utf-8")
    for part in ("# Путь сотрудника", "## Сводка", "Первый рабочий день", "Конец испытательного срока",
                 "Красный флаг", "Ольга (HR)", "## Проверки прогона"):
        assert part in md
    assert "ошибка этапа" not in md


# --- по этапам -------------------------------------------------------------------------------------------

def test_onboarding_is_one_message_then_preboarding(journey):
    st = ok(journey, "onboarding")
    sends = new_sends(journey, st, J.NEWCOMER)
    first = sends[0]
    assert first["mid"] == journey.onboarding_mid and labels(first) == ["На ТЫ", "На ВЫ"]
    # знакомство правится на месте: все ответы на кнопки — в том же сообщении,
    # пятая правка — карточка «Пути новичка» в финал знакомства, когда картинка загрузилась
    ed = edits(journey, st, J.NEWCOMER)
    assert len(ed) == 5 and {e["mid"] for e in ed} == {journey.onboarding_mid}
    assert "Очень приятно с тобой познакомиться, Иван!" in ed[-1]["text"] and labels(ed[-1]) == []
    assert ed[-2]["text"] == ed[-1]["text"] and J.media(ed[-1]["attachments"]) == ["korsik-path.png"]
    # после знакомства — шаги «за 2–3 дня до выхода»
    pre = sends[1:]
    assert 1 <= len(pre) <= 2 and any("документов" in e["text"] for e in pre)
    assert any(J.media(e["attachments"]) for e in pre)
    consents = {c["purpose"]: c["given"] for c in st.new("consents").values()}
    assert consents == {"adaptation": True, "ai_analysis": True}
    d = mine(st.new("deliveries"), journey.emp_id).values()
    assert sum(r["error"] == engine.COVERED_NOTE for r in d) >= 4, "знакомство покрывает шаги 0 и 1.1–1.4"
    assert all(r["status"] == "sent" for r in d)
    assert journey.final["address_form"] == "ty"


def test_day0_buttons_answer_in_place(journey):
    st = ok(journey, "day0")
    sends = new_sends(journey, st, J.NEWCOMER)
    assert any("Да, но немного волнуюсь" in labels(e) for e in sends)
    ed = edits(journey, st, J.NEWCOMER)
    assert len(ed) == 1 and "✅ Да, но немного волнуюсь" in ed[0]["text"] and labels(ed[0]) == []


def test_day1_steps_access_survey_and_lunch(journey):
    st = ok(journey, "day1")
    sends = new_sends(journey, st, J.NEWCOMER)
    files = [f for e in sends for f in J.media(e["attachments"])]
    assert any(f.endswith(".png") for f in files), "шаги первого дня с картинками"
    assert "map_GTP.pdf" in files, "кнопка «Покажи карту» → файл карты площадки"
    assert "food_GTP.pdf" in files, "«Да!» на вопрос про обед → файл своей площадки"
    # опрос про доступы → «Нет» → «Напомнить руководителю» → push руководителю
    answers = list(mine(st.new("answers"), journey.emp_id).values())
    assert [(a["survey"], a["value"], a["mood"]) for a in answers] == [("day1", "Нет", "negative")]
    push = new_sends(journey, st, J.MANAGER)
    assert len(push) == 1 and "пока нет всех доступов" in push[0]["text"]
    assert any("Напомнил руководителю про доступы." in e["text"] for e in edits(journey, st, J.NEWCOMER))
    d = mine(st.new("deliveries"), journey.emp_id).values()
    assert any(r["error"] == engine.EMAIL_NOTE and r["status"] == "skipped" for r in d), "письмо — не в бот"
    assert any(":b" in r["key"] and r["status"] == "sent" for r in d), "шаг-ответ на «Да!» отправлен"
    assert not new_sends(journey, st, J.HR_GROUP)


def test_day1_free_question_card_and_hr_button(journey):
    st = ok(journey, "day1-question")
    card = new_sends(journey, st, J.NEWCOMER)[0]
    assert J.QUESTION in journey.kb_queries
    lines = card["text"].splitlines()
    assert any(ln.startswith("> 6.6.1.") for ln in lines), "дословный пункт ПВТР цитатой"
    assert "Источник: ПВТР, п. 6.6.1" in card["text"] and labels(card) == ["Связаться с HR"]
    assert "Передал вопрос HR" in edits(journey, st, J.NEWCOMER)[-1]["text"]
    (tid, t), = st.new("tickets").items()
    assert t["source"] == "button" and t["category"] == "contact_request" and t["status"] == "new"
    assert t["assignee_id"] == journey.staff_ids["recruiter"]
    (q,) = st.new("questions").values()
    assert q["route"] == "hr" and q["chunk_id"] == journey.chunk_id and q["ticket_id"] == tid
    group = new_sends(journey, st, J.HR_GROUP)
    assert len(group) == 1 and group[0]["text"].startswith(f"Новое обращение #{tid}")


def test_day2_intro_for_colleagues(journey):
    st = ok(journey, "day2")
    texts = [e["text"] for e in new_sends(journey, st, J.NEWCOMER)]
    assert any("представлю тебя коллегам" in t for t in texts), "шаг по отмашке рекрутера"
    assert any("Пришли ещё фотографию" in t for t in texts) and any("представим тебя коллегам" in t for t in texts)
    intro = journey.final["dialog_data"].get("intro") or {}
    assert intro.get("text") == J.INTRO_TEXT and intro.get("photo_url")
    assert len(new_sends(journey, st, J.HR_GROUP)) == 1


def test_day2_intro_published_only_with_consent(journey):
    """«Спасибо» и вопрос о публикации — одно сообщение; публикация в чате GTP только после кнопки."""
    st = ok(journey, "day2")
    ask = new_sends(journey, st, J.NEWCOMER)[-1]
    assert ask["text"].startswith("Спасибо! Передал рекрутеру") and "в чате «Команда GTP»?" in ask["text"]
    assert labels(ask) == ["Опубликовать в чате команды", "Не нужно"]
    log = journey.api.log[st.start:st.end]
    press = next(i for i, e in enumerate(log) if e["op"] == "act" and "Опубликовать в чате команды" in e["text"])
    posts = [i for i, e in enumerate(log) if e["op"] == "send" and e["chat_id"] == J.GTP_CHAT.chat_id]
    assert len(posts) == 1 and posts[0] > press, "без согласия в чат ничего не уходит"
    post = log[posts[0]]
    assert post["text"] == f"Знакомьтесь, это Иван Смирнов! Теперь работает с нами, площадка GTP.\n\n{J.INTRO_TEXT}"
    assert ask["text"].endswith(post["text"]), "согласие давали на этот самый текст"
    assert post["attachments"] == J.PHOTO
    done = [e for e in edits(journey, st, J.NEWCOMER) if e["mid"] == ask["mid"]]
    assert done and done[-1]["text"].endswith("Загляни туда, коллеги наверняка уже здороваются!") and not labels(done[-1])
    assert not new_sends(journey, st, J.GENERAL_CHAT), "общий чат без отметки «для представлений»"
    post_data = journey.final["dialog_data"]["intro_post"]
    assert post_data["status"] == "published" and post_data["chat_id"] == J.GTP_CHAT.chat_id


def test_day3_meeting_push_and_yellow_flag(journey):
    st = ok(journey, "day3")
    push = new_sends(journey, st, J.MANAGER)
    assert len(push) == 1 and "третий день" in push[0]["text"] and labels(push[0])[0] == "Провёл"
    done = edits(journey, st, J.MANAGER)
    assert done and done[-1]["mid"] == push[0]["mid"] and done[-1]["text"].startswith("Встреча проведена ✓")
    task = [b for i, b, a in st.changed("tasks") if a["status"] == "done"]
    assert len(task) == 1 and task[0]["day"] == 3
    # опрос третьего дня одним сообщением, правится на месте
    answers = mine(st.new("answers"), journey.emp_id).values()
    assert [a["value"] for a in answers] == ["Все отлично!", "Пока присматриваюсь", "Пока ничего не понял"]
    assert journey.final["dialog_data"]["surveys"]["day3"].get("done_at")
    assert {e["mid"] for e in edits(journey, st, J.NEWCOMER)} <= {e["mid"] for e in new_sends(journey, st, J.NEWCOMER)}
    (f,) = st.new("flags").values()
    assert f["level"] == "yellow" and f["topic"] == "понимание задач" and f["answer_id"] and not f["ticket_id"]
    assert not st.new("tickets") and not new_sends(journey, st, J.HR_GROUP), "жёлтый флаг HR не дёргает"


def test_day3_red_flag_goes_to_hr_only(journey):
    st = ok(journey, "day3-red")
    flags = list(st.new("flags").values())
    assert flags and all(f["level"] == "red" and f["source"] == "message" for f in flags)
    assert {f["topic"] for f in flags} >= {"угрозы"}
    (tid, t), = st.new("tickets").items()
    assert {f["ticket_id"] for f in flags} == {tid}, "красные флаги одного текста — одно обращение"
    assert t["source"] == "flag" and t["category"] == "harassment" and t["text"] == J.RED_TEXT
    group = new_sends(journey, st, J.HR_GROUP)
    assert len(group) == 1 and group[0]["text"].startswith(f"🔴 Красный флаг, обращение #{tid}")
    (notice,) = new_sends(journey, st, J.NEWCOMER)
    assert "увидит только HR" in notice["text"] and "112" in notice["text"]
    assert J.RED_TEXT not in journey.kb_queries and not st.new("questions"), "не ушло в базу знаний"


def test_day3_hr_reply_and_followup_in_same_ticket(journey):
    st = ok(journey, "day3-hr")
    red_tid = next(iter(journey.stage_by("day3-red").new("tickets")))
    reply = new_sends(journey, st, J.NEWCOMER)
    assert reply[0]["text"].startswith("**Ольга (HR)**") and J.HR_REPLY in reply[0]["text"]
    assert reply[-1]["text"] == "Передал HR."
    (_, before, after), = [c for c in st.changed("tickets") if c[0] == red_tid]
    assert before["status"] == "new" and after["status"] == "in_progress" and after["answered"]
    assert after["assignee_id"] == journey.staff_ids["hr"]
    group_edit = [e for e in edits(journey, st, J.HR_GROUP) if e["mid"] == after["group_mid"]]
    assert group_edit and "В работе: Петрова О." in group_edit[-1]["text"]
    linked = [m for m in st.new("messages").values() if m["direction"] == "in" and m["text"] == J.FOLLOWUP]
    assert linked and linked[0]["ticket_id"] == red_tid
    assert any(e["text"].startswith(f"Новый ответ в обращении #{red_tid}") for e in new_sends(journey, st, J.HR_GROUP))
    assert not st.new("tickets") and not st.new("questions")


def test_day14_week2_survey_buttons_and_text(journey):
    st = ok(journey, "day14")
    first = new_sends(journey, st, J.NEWCOMER)[0]
    assert "img_14.png" in J.media(first["attachments"])
    answers = list(mine(st.new("answers"), journey.emp_id).values())
    assert len(answers) == 9 and all(a["survey"] == "week2" for a in answers)
    assert answers[1]["value"] == "Нет" and answers[1]["text"].startswith("Не хватает инструкции")
    assert [a["score"] for a in answers[2:]] == [9, 8, 9, 8, 10, 9, 10]
    assert journey.final["dialog_data"]["surveys"]["week2"].get("done_at")
    assert not st.new("flags")


def test_month2_surveys_without_time_travel(journey):
    live = [s for s in journey.stages if s.key.startswith("live")]
    for st in live:
        ok(journey, st.key)
    answers = [a for st in live for a in mine(st.new("answers"), journey.emp_id).values()]
    by = Counter(a["survey"] for a in answers)
    assert by == {"plan": 1, "month2": 3}
    multi = next(a for a in answers if a["survey"] == "month2" and ";" in (a["value"] or ""))
    assert multi["value"] == "Руководитель; Коллеги"
    other = next(a for a in answers if a["value"] == "Свой вариант")
    assert other["text"] == "Наставник на смене в первую неделю"
    pushes = [e["text"] for st in live for e in new_sends(journey, st, J.MANAGER)]
    assert any("уже месяц" in t for t in pushes) and any("заканчивает испытательный срок" in t for t in pushes)
    # день опроса month2: «Привет!», подводка и первый вопрос — одно сообщение с картинкой, потом «спасибо»
    day = next(st for st in live if any(a["survey"] == "month2" for a in mine(st.new("answers"), journey.emp_id).values()))
    sends = new_sends(journey, day, J.NEWCOMER)
    assert len(sends) == 2, [e["text"][:60] for e in sends]
    first = sends[0]
    assert first["text"].startswith("Привет!\nМы подготовили небольшой опрос") and "img_08.png" in J.media(first["attachments"])


def test_day60_push_and_step(journey):
    st = ok(journey, "day60")
    push = new_sends(journey, st, J.MANAGER)
    assert len(push) == 1 and "два месяца" in push[0]["text"]
    assert len(new_sends(journey, st, J.NEWCOMER)) == 1


def test_end_final_survey_goodbye_and_all_meetings_done(journey):
    st = ok(journey, "end")
    answers = list(mine(st.new("answers"), journey.emp_id).values())
    assert len(answers) == 12 and all(a["survey"] == "final" for a in answers)
    assert answers[-1]["text"] == "Добавить в первый день экскурсию по цеху с мастером"
    assert journey.final["dialog_data"]["surveys"]["final"].get("done_at")
    sends = new_sends(journey, st, J.NEWCOMER)
    assert any("Спасибо! Мы обязательно учтем твои рекомендации" in e["text"] for e in sends)
    # прощание приходит после опроса, а не вместе с первым вопросом
    bye = next(e for e in sends if "Пока, Корсик! 💙" in labels(e))
    outro = next(i for i, e in enumerate(sends) if "учтем твои рекомендации" in e["text"])
    assert sends.index(bye) > outro
    assert "✅ Пока, Корсик! 💙" in journey.api.msgs[bye["mid"]]["text"]
    tasks = mine(st.db_after["tasks"], journey.emp_id).values()
    assert sorted((t["kind"], t["day"]) for t in tasks if t["kind"] == "meeting") == [
        ("meeting", 3), ("meeting", 30), ("meeting", 60)]
    assert len(tasks) == 4 and all(t["status"] == "done" and t["mid"] for t in tasks), "все встречи с push и «Провёл»"
    flags = journey.stage_by("end").db_after["flags"]
    assert Counter(f["level"] for f in mine(flags, journey.emp_id).values()) == {"red": 2, "yellow": 1}


# --- «Путь новичка»: вехи без новых сообщений ------------------------------------------------------------

def test_path_milestones_ride_on_existing_messages(journey):
    """Карточка пути и строка о вехе — в уже существующих сообщениях: новых сообщений новичку столько же (39)."""
    route = [st for st in journey.stages if st.key != "setup"]
    assert sum(len(new_sends(journey, st, J.NEWCOMER)) for st in route) == 39, "число на слайде и в речи"

    newcomer = [e for e in journey.api.log if e["op"] in ("send", "answer", "edit")
                and e["chat_id"] == J.NEWCOMER.chat_id and "korsik-path.png" in J.media(e["attachments"])]
    lines = {re.search(r"Веха (\d) из 7 пройдена", e["text"]).group(1) for e in newcomer}
    assert lines == {"1", "2", "3", "4", "5", "6", "7"}, "все вехи: знакомство и 6 опросов"
    onboarding = journey.stage_by("onboarding")
    intro = [e for e in edits(journey, onboarding, J.NEWCOMER) if "Веха 1 из 7" in e["text"]]
    assert intro and intro[-1]["text"].startswith("Очень приятно"), "веха 1 — в финале знакомства"
    assert "Дальше: «Первый день»" in intro[-1]["text"]
    final = [e for e in newcomer if "Веха 7 из 7" in e["text"]]
    assert final and all("Спасибо! Мы обязательно учтем твои рекомендации" in e["text"] for e in final)
    for e in newcomer:
        assert not re.search("подар|мерч|сюрприз", e["text"]), "новичку про подарок заранее не говорим"


def test_path_merch_to_hr_group_once_at_the_end(journey):
    merch = [(st.key, e) for st in journey.stages for e in new_sends(journey, st, J.HR_GROUP)
             if "Путь адаптации пройден" in e["text"]]
    assert [k for k, _ in merch] == ["end"], "один раз и только после последней вехи"
    text = merch[0][1]["text"]
    assert text.startswith("Путь адаптации пройден: Иван Смирнов, площадка GTP.")
    assert "термокружк" in text
    for quote in (J.RED_TEXT, "Добавить в первый день экскурсию", "Не хватает инструкции"):
        assert quote not in text, "ответы и жалобы в сообщение про мерч не попадают"
    assert journey.final["dialog_data"]["path"]["hr_finished_at"]


# --- вне маршрута: найденные баги ---------------------------------------------------------------------------

def test_red_flag_inside_hr_reply_window(journey):
    """Угроза в окне ответа HR даёт красный флаг, а не уходит «ответом» в старое обращение (порядок плагинов)."""
    st = ok(journey, "edge-window-red")
    emp_id = journey.edge_ids["petr"]
    red = [f for f in mine(st.new("flags"), emp_id).values() if f["level"] == "red"]
    assert red, "угроза в окне ответа HR без красного флага"
    assert any("увидит только HR" in e["text"] for e in new_sends(journey, st, J.PETR))


def test_reply_window_closed_after_time_travel(journey):
    """Окно ответа HR живёт в настоящем времени: после перемотки новый вопрос идёт в базу знаний."""
    st = ok(journey, "edge-window-travel")
    emp_id = journey.edge_ids["anna"]
    asked = [q for q in mine(st.new("questions"), emp_id).values() if q["question"] == J.QUESTION]
    assert asked, "вопрос через две недели (по машине времени) не дошёл до базы знаний"


def test_edge_scenarios_ran(journey):
    """Сами проверки вне маршрута отработали без исключений (иначе проверки выше ничего не доказывают)."""
    for key in ("edge-window-red", "edge-window-travel"):
        ok(journey, key)
    petr = journey.edge_ids["petr"]
    st = journey.stage_by("edge-window-red")
    follow = [m for m in st.new("messages").values()
              if m["employee_id"] == petr and m["direction"] == "in" and m["text"] == J.EDGE_RED]
    assert follow, "текст об угрозе не записан"
    assert new_sends(journey, st, J.HR_GROUP), "HR-группа ничего не узнала"
