"""Правила риска на фактах без БД: каждое правило, уровень, тексты причин."""

from datetime import UTC, date, datetime, timedelta

from app.analytics import rules as R
from app.analytics.risks import workdays_after
from app.analytics.stages import stage_of

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def flag(level: str, topic: str, *, status: str = "new", source: str = "survey", days: float = 1) -> R.FlagFact:
    return R.FlagFact(level=level, topic=topic, status=status, source=source, created_at=ago(days))


def codes(f: R.Facts) -> list[str]:
    return [r.code for r in R.evaluate(f, NOW)[1]]


def test_no_signals_no_risk():
    assert R.evaluate(R.Facts(day=10), NOW) == (None, [])


def test_red_flag_is_high_and_resolved_is_ignored():
    level, reasons = R.evaluate(R.Facts(day=10, flags=[flag("red", "травля", days=2)]), NOW)
    assert level == "high"
    assert reasons[0].text == "Красный сигнал: травля, 2 дня назад"
    assert R.evaluate(R.Facts(day=10, flags=[flag("red", "травля", status="resolved")]), NOW) == (None, [])
    two = R.Facts(day=10, flags=[flag("red", "травля", days=3), flag("red", "угрозы", days=1)])
    assert R.evaluate(two, NOW)[1][0].text == "2 красных сигнала: угрозы, травля"


def test_intent_from_button_or_text_is_high():
    by_button = R.Facts(day=88, flags=[flag("red", "намерение уйти", source="button", days=1)])
    level, reasons = R.evaluate(by_button, NOW)
    assert level == "high" and [r.code for r in reasons] == ["intent"]  # не дублируется красным сигналом
    assert reasons[0].text == "Думает об уходе: ответ в опросе вчера"
    by_text = R.Facts(day=40, flags=[flag("yellow", "намерение уйти", days=8)])
    level, reasons = R.evaluate(by_text, NOW)
    assert level == "high" and reasons[0].text == "Думает об уходе: сообщение 8 дней назад"
    assert codes(by_text) == ["intent"]  # жёлтый сигнал «намерение уйти» не считаем второй раз


def test_intent_resolved_counts_for_30_days():
    recent = R.Facts(day=60, flags=[flag("yellow", "намерение уйти", status="resolved", days=20)])
    old = R.Facts(day=60, flags=[flag("yellow", "намерение уйти", status="resolved", days=40)])
    assert codes(recent) == ["intent"]
    assert codes(old) == []


def test_unsure_to_stay_is_medium():
    level, reasons = R.evaluate(R.Facts(day=89, stay_unsure_at=ago(0)), NOW)
    assert level == "medium" and reasons[0].text == "Сомневается, что останется после испытательного"


def test_yellow_flags_medium():
    f = R.Facts(day=12, flags=[flag("yellow", "нагрузка", days=3), flag("yellow", "понимание задач", days=1)])
    level, reasons = R.evaluate(f, NOW)
    assert level == "medium"
    assert reasons[0].text == "2 жёлтых сигнала: понимание задач, нагрузка"


def score(code: str, avg: float, days: float) -> R.SurveyScore:
    return R.SurveyScore(code=code, title={"week2": "Чек через 2 недели"}.get(code, code), at=ago(days), avg=avg)


def test_score_drop_is_high():
    level, reasons = R.evaluate(R.Facts(day=89, scores=[score("week2", 9.0, 70), score("final", 5.0, 0)]), NOW)
    assert level == "high"
    assert [r.text for r in reasons] == ["Оценка упала с 9 до 5"]  # низкая оценка не повторяется


def test_drop_less_than_3_is_not_a_drop():
    f = R.Facts(day=89, scores=[score("week2", 8.4, 70), score("final", 6.2, 0)])
    assert codes(f) == []


def test_low_last_score_is_medium():
    level, reasons = R.evaluate(R.Facts(day=20, scores=[score("week2", 5.14, 5)]), NOW)
    assert level == "medium"
    assert reasons[0].text == "Оценка 5,1 из 10 в опросе «Чек через 2 недели»"
    # оценка поднялась: прошлое не в счёт
    assert codes(R.Facts(day=89, scores=[score("week2", 4.0, 70), score("final", 7.0, 0)])) == []


def survey(title: str, status: str, days: float) -> R.SurveyState:
    return R.SurveyState(title=title, status=status, on=(NOW - timedelta(days=days)).date())


def test_two_missed_in_a_row():
    f = R.Facts(day=40, last_activity=ago(1), surveys=[
        survey("День 3", "done", 37), survey("Чек 2 недели", "missed", 26), survey("План", "missed", 8),
        survey("Свежий", "pending", 1)])
    level, reasons = R.evaluate(f, NOW)
    assert level == "medium" and reasons[0].text == "2 опроса подряд без ответа"
    # пропуски не подряд — не риск
    mixed = R.Facts(day=40, last_activity=ago(1), surveys=[
        survey("День 3", "missed", 37), survey("Чек 2 недели", "done", 26), survey("План", "missed", 8)])
    assert codes(mixed) == []


def test_silent_only_when_bot_waits_for_answer():
    waiting = [survey("День 1", "done", 19), survey("Чек через 2 недели", "missed", 5)]
    f = R.Facts(day=20, last_activity=ago(18), surveys=waiting)
    assert R.evaluate(f, NOW)[1][0].text == "Молчит 18 дней, нет ответа на «Чек через 2 недели»"
    # молчит, но все опросы пройдены — просто нет повода писать
    calm = R.Facts(day=40, last_activity=ago(20), surveys=[survey("Чек", "done", 21)])
    assert codes(calm) == []
    # отвечал недавно
    assert codes(R.Facts(day=20, last_activity=ago(3), surveys=waiting)) == []
    # бот ему не пишет: не подключился или ещё не вышел
    assert "silent" not in codes(R.Facts(day=20, connected=False, last_activity=ago(18), surveys=waiting))


def test_not_connected_from_day_before_start():
    assert codes(R.Facts(day=-1, connected=False, status="waiting")) == []
    assert R.evaluate(R.Facts(day=0, connected=False), NOW)[1][0].text == "Выход завтра, а в боте ещё нет"
    level, reasons = R.evaluate(R.Facts(day=4, connected=False), NOW)
    assert level == "medium" and reasons[0].text == "В боте ещё нет, хотя выход был 3 дня назад"
    assert R.evaluate(R.Facts(day=6, connected=False), NOW)[0] == "high"


def test_meeting_overdue_more_than_3_workdays():
    m = R.Meeting(title="Встреча на 30-й день", due=date(2026, 10, 14), overdue_workdays=4)
    level, reasons = R.evaluate(R.Facts(day=40, meetings=[m]), NOW)
    assert level == "medium" and reasons[0].text == "Встреча на 30-й день: просрочена на 4 рабочих дня"
    m3 = R.Meeting(title="Встреча на 3-й день", due=date(2026, 10, 20), overdue_workdays=3)
    assert codes(R.Facts(day=6, meetings=[m3])) == []


def test_ticket_without_hr_answer_more_than_a_day():
    assert codes(R.Facts(day=10, tickets_waiting=[ago(0.5)])) == []
    assert R.evaluate(R.Facts(day=10, tickets_waiting=[ago(1.2)]), NOW)[1][0].text == "Нет ответа HR больше суток"
    two = R.Facts(day=10, tickets_waiting=[ago(2.1), ago(1.5)])
    assert R.evaluate(two, NOW)[1][0].text == "Нет ответа HR 2 дня, обращений: 2"


def test_bot_stopped_is_high_and_not_counted_as_not_connected():
    level, reasons = R.evaluate(R.Facts(day=10, status="stopped", connected=False), NOW)
    assert level == "high" and [r.code for r in reasons] == ["bot_stopped"]


def test_three_medium_reasons_make_high_and_high_goes_first():
    f = R.Facts(day=40, flags=[flag("yellow", "нагрузка")], tickets_waiting=[ago(3)],
                meetings=[R.Meeting(title="Встреча на 30-й день", due=date(2026, 10, 10), overdue_workdays=9)])
    assert R.evaluate(f, NOW)[0] == "high"
    f.scores = [score("week2", 9, 30), score("final", 4, 0)]
    assert R.evaluate(f, NOW)[1][0].code == "score_drop"


def test_reason_dict_has_label():
    r = R.Reason("score_drop", "high", "Оценка упала с 9 до 5", NOW).to_dict()
    assert r == {"code": "score_drop", "level": "high", "label": "падение оценок", "text": "Оценка упала с 9 до 5",
                 "at": NOW.isoformat()}


def test_words():
    assert R.num(9.0) == "9" and R.num(8.64) == "8,6" and R.num(5.25) == "5,2"
    assert R.ago(NOW, NOW) == "сегодня" and R.ago(ago(1.5), NOW) == "вчера" and R.ago(ago(5), NOW) == "5 дней назад"
    assert R.ago(ago(21), NOW) == "21 день назад" and R.ago(ago(3), NOW) == "3 дня назад"


def test_stages():
    assert [stage_of(d, 90) for d in (-3, 0, 1, 7, 8, 30, 31, 60, 61, 90, 91)] == [
        "pre", "pre", "week1", "week1", "month1", "month1", "month2", "month2", "month3", "month3", "done"]
    assert stage_of(120, 180) == "month3"  # испытательный 6 месяцев
    assert stage_of(10, 90, "finished") == "done"


def test_workdays_after_skips_weekend():
    fri, mon = date(2026, 10, 16), date(2026, 10, 19)
    assert workdays_after(fri, fri) == 0
    assert workdays_after(fri, date(2026, 10, 18)) == 0  # суббота и воскресенье
    assert workdays_after(fri, mon) == 1
    assert workdays_after(fri, date(2026, 10, 23)) == 5
