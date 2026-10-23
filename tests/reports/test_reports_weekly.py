"""Еженедельная сводка: период, порог «5 ответивших», объединение мелких площадок, подпись, отправка."""

import json
import struct
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.bot import runtime
from app.bot.api import MaxApiError
from app.reports import render, weekly

MON = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)   # понедельник, 09:00 МСК
THU = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

SITES = [("GTP", "Площадка GTP"), ("GrPP", "Группа ПОЛИПЛАСТИК (GrPP)"), ("Luzhniki", "Офис «Лужники»")]


def make_raw(respondents: dict[str, int], *, newbies: int = 7, answers_each: int = 3, mood=None,
             topics=("доступы", "нагрузка", "доступы")) -> weekly.Raw:
    raw = weekly.Raw(sites=list(SITES))
    eid = 0
    for code, n_resp in respondents.items():
        for i in range(newbies):
            eid += 1
            raw.employees.append({"id": eid, "site": code, "start_date": date(2026, 10, 1),
                                  "probation_end": date(2027, 1, 1), "status": "active", "synthetic": True})
            if i < n_resp:
                for j in range(answers_each):
                    raw.answers.append({"employee_id": eid, "site": code, "score": 9 if j else 6, "mood": mood,
                                        "topic": topics[j % len(topics)], "synthetic": True})
    return raw


# --- период ------------------------------------------------------------------

def test_period_on_monday_is_previous_week():
    p = weekly.period(MON)
    assert (p["week_start"], p["week_end"]) == (date(2026, 10, 12), date(2026, 10, 18))
    assert p["window_start"] == date(2026, 9, 21)
    assert p["to_dt"].isoformat() == "2026-10-19T00:00:00+03:00"


def test_period_midweek_ends_today():
    p = weekly.period(THU)
    assert (p["week_start"], p["week_end"]) == (date(2026, 10, 16), date(2026, 10, 22))


def test_range_label():
    assert weekly.range_label(date(2026, 10, 12), date(2026, 10, 18), year=True) == "12–18 октября 2026"
    assert weekly.range_label(date(2026, 9, 28), date(2026, 10, 4)) == "28 сентября – 4 октября"


def test_mood_of():
    assert weekly.mood_of("позитив", 3) == "positive"
    assert weekly.mood_of("negative", None) == "negative"
    assert weekly.mood_of(None, 8) == "positive"
    assert weekly.mood_of(None, 6) == "neutral"
    assert weekly.mood_of(None, 4) == "negative"
    assert weekly.mood_of(None, None) is None


# --- порог и объединение -----------------------------------------------------

def test_sites_above_threshold_are_shown():
    rep = weekly.aggregate(make_raw({"GTP": 5, "GrPP": 6, "Luzhniki": 5}), weekly.period(MON))
    assert [r["code"] for r in rep["mood_rows"]] == ["GrPP", "GTP", "Luzhniki"]
    assert rep["low_data"] == [] and rep["merged"] is None
    gtp = next(s for s in rep["sites"] if s["code"] == "GTP")
    assert gtp["respondents"] == 5 and gtp["avg_score"] == 8.0
    assert gtp["mood"] == {"positive": 10, "neutral": 5, "negative": 0}


def test_small_site_is_hidden_with_low_data_mark():
    rep = weekly.aggregate(make_raw({"GTP": 6, "GrPP": 5, "Luzhniki": 4}), weekly.period(MON))
    assert "Luzhniki" not in [r["code"] for r in rep["mood_rows"]]
    assert rep["low_data"] == ["Офис «Лужники»"]
    assert rep["merged"]["shown"] is False


def test_small_sites_are_merged():
    rep = weekly.aggregate(make_raw({"GTP": 6, "GrPP": 3, "Luzhniki": 3}), weekly.period(MON))
    merged = rep["mood_rows"][-1]
    assert merged["low_data"] and merged["name"] == "Остальные площадки" and merged["respondents"] == 6
    assert rep["low_data"] == []
    assert rep["merged"]["codes"] == ["GrPP", "Luzhniki"]


def test_overall_below_threshold_hides_scores_and_topics():
    rep = weekly.aggregate(make_raw({"GTP": 2, "GrPP": 2}), weekly.period(MON))
    assert rep["kpi"]["avg_score"] is None and rep["topics"] == [] and rep["mood_rows"] == []
    assert rep["kpi"]["respondents"] == 4


def test_threshold_counts_people_not_answers():
    rep = weekly.aggregate(make_raw({"GTP": 4}, answers_each=10), weekly.period(MON))
    assert rep["mood_rows"] == [] and rep["low_data"] == ["Площадка GTP"]


def test_topics_top3_without_other():
    raw = make_raw({"GTP": 5, "GrPP": 5}, topics=("доступы", "другое", "нагрузка"), mood="negative")
    raw.questions = [{"employee_id": 1, "site": "GTP", "topic": "быт и транспорт"}] * 2
    rep = weekly.aggregate(raw, weekly.period(MON))
    assert rep["topics"] == [{"topic": "Доступы", "count": 10}, {"topic": "Нагрузка", "count": 10},
                             {"topic": "Быт и транспорт", "count": 2}]


def test_topics_skip_positive_button_answers():
    """Кнопка «всё хорошо» не тема: иначе топ повторяет набор вопросов опроса. Текст и вопросы боту — темы."""
    # оценки 6 (так себе) у «доступов», 9 (хорошо) у «коллектива» и «нагрузки»
    raw = make_raw({"GTP": 5, "GrPP": 5}, topics=("доступы", "коллектив", "нагрузка"))
    for a in raw.answers:
        if a["topic"] == "нагрузка" and a["employee_id"] % 2:
            a["text"] = True  # свой текст в ответе — тема, даже если оценка хорошая
    raw.questions = [{"employee_id": 1, "site": "GTP", "topic": "зарплата"}] * 3
    rep = weekly.aggregate(raw, weekly.period(MON))
    # ответили 10 новичков (id 1–5 и 8–12), свой текст про нагрузку — у нечётных: 5 ответов
    assert rep["topics"] == [{"topic": "Доступы", "count": 10}, {"topic": "Нагрузка", "count": 5},
                             {"topic": "Зарплата", "count": 3}]
    assert weekly.topic_worthy({"mood": "positive", "score": 10}) is False
    assert weekly.topic_worthy({"mood": None, "score": None}) is False
    assert weekly.topic_worthy({"mood": "негатив", "score": None}) is True


def test_kpi_newbies_and_signals():
    raw = make_raw({"GTP": 5})
    raw.employees.append({"id": 900, "site": "GTP", "start_date": date(2026, 10, 14), "probation_end": None,
                          "status": "active", "synthetic": True})
    raw.employees.append({"id": 901, "site": "GTP", "start_date": date(2026, 3, 1),
                          "probation_end": date(2026, 6, 1), "status": "finished", "synthetic": True})
    raw.flags = [{"employee_id": 1, "site": "GTP", "status": "new", "level": "red"},
                 {"employee_id": 2, "site": "GTP", "status": "resolved", "level": "yellow"}]
    k = weekly.aggregate(raw, weekly.period(MON))["kpi"]
    assert k["newbies"] == 8 and k["newbies_week"] == 1
    assert (k["signals_open"], k["signals_red_open"], k["signals_closed"]) == (1, 1, 1)


def test_report_has_no_names_or_quotes():
    raw = make_raw({"GTP": 5, "GrPP": 5})
    rep = weekly.aggregate(raw, weekly.period(MON))
    dumped = json.dumps(rep, ensure_ascii=False, default=str)
    assert "quote" not in dumped and "full_name" not in dumped and "employee_id" not in dumped


def test_caption():
    rep = weekly.aggregate(make_raw({"GTP": 5, "GrPP": 5}), weekly.period(MON))
    text = weekly.caption(rep, MON)
    assert text.startswith("Доброе утро! Сводка по новичкам за 12–18 октября 2026.")
    assert "В адаптации 14 человек, средняя оценка 8,0 из 10, открытых сигналов: 0." in text
    assert "Данные синтетические." in text and "—" not in text


# --- картинка ----------------------------------------------------------------

def test_render_html_shows_low_data_line():
    rep = weekly.aggregate(make_raw({"GTP": 6, "GrPP": 5, "Luzhniki": 2}), weekly.period(MON))
    html = render.render_html(rep)
    assert "12–18 октября 2026" in html and "Мало данных:" in html and "Офис «Лужники»" in html
    assert "tabular-nums" in html and "gradient" not in html


@pytest.mark.asyncio
async def test_render_png_size():
    rep = weekly.aggregate(make_raw({"GTP": 6, "GrPP": 5, "Luzhniki": 5}), weekly.period(MON))
    png = await render.render_png(rep)
    png2 = await render.render_png(rep)   # браузер переиспользуется
    await render.close()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1080, 1350)
    assert len(png2) > 10_000


# --- отправка ----------------------------------------------------------------

class FakeScalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, staff):
        self.staff = staff

    async def scalars(self, stmt):
        return FakeScalars(self.staff)


@pytest.mark.asyncio
async def test_send_uploads_image_and_retries_not_ready(monkeypatch):
    uploads, sent = [], []
    fails = [MaxApiError(400, "attachment.not.ready", "not ready")]

    async def upload(kind, filename, data, ctype):
        uploads.append((kind, filename, ctype, len(data)))
        return {"photos": {"p1": {"token": "tok"}}}

    async def call(key, fn, *a, **kw):
        return await fn(*a, **kw)

    async def fake_send(repo, text, *, staff=None, attachments=None, **kw):
        if fails:
            raise fails.pop()
        sent.append((staff.id, text, attachments))
        return ["m1"]

    async def fake_build(session, now=None, *, week_end=None):
        rep = weekly.aggregate(make_raw({"GTP": 5, "GrPP": 5}), weekly.period(MON))
        return rep

    async def fake_png(report):
        return b"png"

    async def no_sleep(_):
        return None

    ctx = SimpleNamespace(sender=SimpleNamespace(call=call, api=SimpleNamespace(upload=upload)))
    monkeypatch.setattr(runtime, "get_ctx", lambda: ctx)
    monkeypatch.setattr(runtime, "send", fake_send)
    monkeypatch.setattr(weekly, "build", fake_build)
    monkeypatch.setattr(render, "render_png", fake_png)
    monkeypatch.setattr(weekly, "_sleep", no_sleep)
    monkeypatch.setattr(weekly.settings, "miniapp_url", "")

    head = SimpleNamespace(id=4, role="head_recruitment", chat_id=55, max_user_id=5)
    unbound = SimpleNamespace(id=6, role="head_recruitment", chat_id=None, max_user_id=None)
    assert await weekly.send(FakeSession([head, unbound]), MON)
    assert uploads == [("image", "korsik-weekly.png", "image/png", 3)]
    assert len(sent) == 1 and sent[0][0] == 4
    assert sent[0][2] == [{"type": "image", "payload": {"photos": {"p1": {"token": "tok"}}}}]
    assert sent[0][1].startswith("Доброе утро! Сводка по новичкам")


@pytest.mark.asyncio
async def test_send_without_head_or_bot(monkeypatch):
    monkeypatch.setattr(runtime, "get_ctx", lambda: None)
    assert not await weekly.send(FakeSession([]))
    head = SimpleNamespace(id=4, role="head_recruitment", chat_id=55, max_user_id=5)
    assert not await weekly.send(FakeSession([head]))


def test_window_is_four_weeks():
    p = weekly.period(MON)
    assert p["week_end"] - p["window_start"] == timedelta(days=27)


# --- порог «5 разных новичков» для каждой цифры ---------------------------------

def _person(raw: weekly.Raw, eid: int, code: str = "GTP") -> None:
    raw.employees.append({"id": eid, "site": code, "start_date": date(2026, 10, 1),
                          "probation_end": date(2027, 1, 1), "status": "active", "synthetic": True})


def test_avg_score_needs_five_scored_newbies():
    """5 новичков прошли кнопочные опросы, до шкалы дошёл один: его оценку как среднюю не показываем."""
    raw = weekly.Raw(sites=list(SITES))
    for eid in range(1, 6):
        _person(raw, eid)
        raw.answers.append({"employee_id": eid, "site": "GTP", "score": None, "mood": "positive",
                            "topic": "коллектив", "synthetic": True})
    raw.answers.append({"employee_id": 1, "site": "GTP", "score": 2, "mood": None, "topic": "нагрузка",
                        "synthetic": True})
    rep = weekly.aggregate(raw, weekly.period(MON))
    gtp = rep["mood_rows"][0]
    assert gtp["respondents"] == 5 and gtp["scores"] == 1
    assert gtp["avg_score"] is None and rep["kpi"]["avg_score"] is None
    assert gtp["mood_total"] == 6 and gtp["mood_share"]["positive"] == pytest.approx(5 / 6)  # настроение — у пятерых
    html = render.render_html(rep)
    assert "2,0" not in html


def test_mood_needs_five_newbies_with_mood():
    """Ответили пятеро, настроение понятно только у двоих: доли настроения нет, средняя оценка есть."""
    raw = weekly.Raw(sites=list(SITES))
    for eid in range(1, 6):
        _person(raw, eid)
        raw.answers.append({"employee_id": eid, "site": "GTP", "score": None, "mood": None, "topic": "доступы",
                            "synthetic": True})
        raw.answers.append({"employee_id": eid, "site": "GTP", "score": 7, "mood": "нейтрально" if eid > 3 else
                            "unknown", "topic": "доступы", "synthetic": True})
    for a in raw.answers:
        if a["score"] is not None and a["mood"] == "unknown":
            a["score"] = None  # у троих шкалы нет, а настроение не распознано
            a["mood"] = None
    rep = weekly.aggregate(raw, weekly.period(MON))
    gtp = rep["mood_rows"][0]
    assert gtp["respondents"] == 5
    assert gtp["avg_score"] is None  # оценка тоже только у двоих
    assert gtp["mood_total"] == 0 and gtp["mood_share"]["positive"] is None
    assert rep["overall"]["mood_share"]["neutral"] is None
    html = render.render_html(rep)
    assert 'share num">—<' in html and 'share num">0%' not in html


def test_kpi_note_counts_scored_newbies():
    """Подпись под средней оценкой — сколько новичков поставили оценку, а не сколько ответили вообще."""
    raw = make_raw({"GTP": 5, "GrPP": 5})
    for eid in range(100, 103):
        _person(raw, eid)
        raw.answers.append({"employee_id": eid, "site": "GTP", "score": None, "mood": "positive",
                            "topic": "коллектив", "synthetic": True})
    rep = weekly.aggregate(raw, weekly.period(MON))
    assert rep["kpi"]["respondents"] == 13 and rep["kpi"]["score_respondents"] == 10
    assert 'note num">10 оценили<' in render.render_html(rep)


# --- догон сводки после простоя -----------------------------------------------------

def test_due_week_end():
    assert weekly.due_week_end(MON - timedelta(minutes=1)) == date(2026, 10, 11)  # пн 08:59 МСК — ещё рано
    assert weekly.due_week_end(MON) == date(2026, 10, 18)
    assert weekly.due_week_end(THU) == date(2026, 10, 18)
    assert weekly.due_week_end(datetime(2026, 10, 25, 20, 0, tzinfo=UTC)) == date(2026, 10, 18)  # вс 23:00 МСК


def test_period_for_given_week():
    p = weekly.period(THU, week_end=date(2026, 10, 18))
    assert (p["week_start"], p["week_end"]) == (date(2026, 10, 12), date(2026, 10, 18))


@pytest.mark.asyncio
async def test_send_builds_given_week(monkeypatch):
    weeks = []

    async def fake_build(session, now=None, *, week_end=None):
        weeks.append(week_end)
        return weekly.aggregate(make_raw({"GTP": 5}), weekly.period(now, week_end=week_end))

    async def call(key, fn, *a, **kw):
        return {"token": "t"}

    async def fake_send(repo, text, *, staff=None, attachments=None, **kw):
        weeks.append(text)
        return ["m1"]

    async def fake_png(report):
        return b"png"

    ctx = SimpleNamespace(sender=SimpleNamespace(call=call, api=SimpleNamespace(upload=None)))
    monkeypatch.setattr(runtime, "get_ctx", lambda: ctx)
    monkeypatch.setattr(runtime, "send", fake_send)
    monkeypatch.setattr(weekly, "build", fake_build)
    monkeypatch.setattr(render, "render_png", fake_png)
    monkeypatch.setattr(weekly.settings, "miniapp_url", "")
    head = SimpleNamespace(id=4, role="head_recruitment", chat_id=55, max_user_id=5)
    assert await weekly.send(FakeSession([head]), THU, week_end=date(2026, 10, 18))
    assert weeks[0] == date(2026, 10, 18)
    assert weeks[1].startswith("Добрый день! Сводка по новичкам за 12–18 октября 2026.")


def test_catchup_job_registered():
    import app.reports.hooks  # noqa: F401 — регистрирует задачи
    from app.jobs import registered

    jobs = {j.name: j for j in registered()}
    assert jobs["weekly_report"].cron == {"day_of_week": "mon", "hour": 9, "minute": 0}
    assert jobs["weekly_report_catchup"].seconds and jobs["weekly_report_catchup"].seconds <= 3600


def test_row_counts_answered_veterans():
    """Испытательный закончился на этой неделе, а итоговый опрос он прошёл: «N из M» не бывает N > M."""
    raw = make_raw({"GTP": 5})
    for eid in (200, 201):
        raw.employees.append({"id": eid, "site": "GTP", "start_date": date(2026, 7, 10),
                              "probation_end": date(2026, 10, 14), "status": "active", "synthetic": True})
        raw.answers.append({"employee_id": eid, "site": "GTP", "score": 8, "mood": None, "topic": "нагрузка",
                            "synthetic": True})
    rep = weekly.aggregate(raw, weekly.period(MON))
    gtp = rep["mood_rows"][0]
    assert gtp["respondents"] == 7 and gtp["newbies"] == 9
    assert rep["kpi"]["newbies"] == 7  # в адаптации сейчас — без тех, у кого испытательный закончился
