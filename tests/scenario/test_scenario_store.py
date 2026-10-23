"""Сценарий в БД: стабильные ключи, upsert, выключение удалённых шагов, площадки."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import ScenarioStep, Site
from app.scenario import store
from app.scenario.models import Button, StepSpec, TimingRule

XLSX = Path("data/raw/korsik_scenario.xlsx")


def S(n, rule, **kw):
    return StepSpec(number=n, timing=TimingRule(**rule), text=kw.pop("text", f"шаг {n}"), **kw)


def renumber(specs):
    """Нумерация 1..N после правки списка, как у импортёра (after_step — на новый номер вопроса)."""
    old_to_new = {sp.number: i for i, sp in enumerate(specs, 1)}
    for sp in specs:
        if sp.timing and sp.timing.after_step:
            sp.timing.after_step = old_to_new[sp.timing.after_step]
        sp.number = old_to_new[sp.number]
    return specs


def base_specs():
    return [
        S(1, {"kind": "before_start", "days_before": 3}, code="1.1"),
        S(2, {"kind": "before_start", "days_before": 3}, site_tags=["GTP"], attachment="img_03.png"),
        S(3, {"kind": "workday", "n": 1, "time": "12:30"},
          buttons=[Button(label="Да!", action="branch", next_step=4), Button(label="Нет", reply="Понял")]),
        S(4, {"kind": "event", "event": "answer", "after_step": 3, "answer": "Да"}, site_tags=["GTP"],
          attachment="food_GTP.pdf"),
        S(5, {"kind": "workday", "n": 3, "time": "17:30"}, kind="survey"),
        S(6, {"kind": "workday", "n": 3, "time": "17:30"}, text="Спасибо за обратную связь!"),
    ]


def test_keys_survive_text_edit_and_insert_elsewhere():
    specs = base_specs()
    keys = store.step_keys(specs)
    assert keys[1] == "pre3@1000|all|1"
    assert keys[2] == "pre3@1000|GTP|1"
    assert keys[3] == "wd1@1230|all|1"
    assert keys[4].startswith("ans.") and keys[4].endswith("|GTP|1")

    # HR поправил текст и вставил шаг в другой момент — ключи прежние
    edited = base_specs()
    edited[0].text = "новый текст"
    edited.insert(2, S(99, {"kind": "workday", "n": 2}))
    new_keys = store.step_keys(renumber(edited))
    assert set(keys.values()) <= set(new_keys.values())


def test_link_hint_and_flags():
    text = 'И напоследок кидаю тебе плейлист! 🎶\n\n(наложить на слово "плейлист" гиперссылку на https://music.example/1 )'
    assert store._apply_link_hints(text) == "И напоследок кидаю тебе [плейлист](https://music.example/1)! 🎶"
    specs = base_specs()
    keys = store.step_keys(specs)
    after = store._after_survey(specs, keys)
    assert after == {6: keys[5]}
    values = store.row_values(specs[0], keys, after)
    assert values["extra"]["covered"] is True
    assert values["send_time"] is None or values["send_time"].hour == 10


@pytest.mark.asyncio
async def test_import_upsert_deactivate_and_sites(db):
    async with db() as s:
        rep = await store.import_specs(s, base_specs())
        await s.commit()
        assert (rep.created, rep.updated, rep.deactivated) == (6, 0, 0)
        assert rep.new_sites == []
        assert rep.surveys == 1 and rep.attachments == 2

    # повторный импорт: шаг 2 удалён, у шага 1 новый текст, новая площадка NEW
    specs = base_specs()
    specs[0].text = "Правка HR"
    del specs[1]
    specs.append(S(7, {"kind": "workday", "n": 1}, site_tags=["NEW"]))
    renumber(specs)
    async with db() as s:
        rep = await store.import_specs(s, specs)
        await s.commit()
        assert rep.created == 1 and rep.deactivated == 1 and rep.updated == 5
        assert rep.new_sites == ["NEW"]
        rows = {r.key: r for r in (await s.scalars(select(ScenarioStep))).all()}
        assert rows["pre3@1000|all|1"].text == "Правка HR"
        assert rows["pre3@1000|GTP|1"].is_active is False
        assert await store.pending_sites(s) == ["NEW"]
        with pytest.raises(ValueError):
            await store.confirm_site(s, "NEW", "Новая", "Mars/Olympus")
        site = await store.confirm_site(s, "NEW", "Новая площадка", "Asia/Yekaterinburg")
        await s.commit()
        assert site.tz == "Asia/Yekaterinburg"
        assert await store.pending_sites(s) == []
        assert (await s.get(Site, "NEW")).name == "Новая площадка"


@pytest.mark.asyncio
@pytest.mark.skipif(not XLSX.exists(), reason="нет data/raw/korsik_scenario.xlsx")
async def test_import_real_xlsx(db):
    async with db() as s:
        rep = await store.import_xlsx(s, XLSX)
        await s.commit()
        assert rep.steps == 68 and rep.created == 68
        assert rep.new_sites == []
        assert not [w for w in rep.warnings if "время" in w]
        rows = (await s.scalars(select(ScenarioStep).order_by(ScenarioStep.order_no))).all()
        assert len({r.key for r in rows}) == 68
        by = {r.order_no: r for r in rows}
        assert [r.order_no for r in rows if r.extra["covered"]] == [1, 2, 3, 4, 5, 6]
        assert "[плейлист](https://music.yandex.ru" in by[65].text
        assert by[65].extra["after_survey"] == by[60].key and by[66].extra["after_survey"] == by[60].key
        assert any(b.get("followup") == "remind_access" for b in by[20].buttons)
        assert by[17].extra["channel"] == "email"
        assert by[47].kind == "survey"  # поправка из config/scenario_overrides.yaml
        titles = {n: by[n].extra["title"] for n in (14, 37, 38, 45, 51, 21)}
        assert titles == {14: "Ты готов к первому дню?", 37: "Опрос 3-го дня", 38: "Опрос 3-го дня",
                          45: "Чек второй недели", 51: "Опрос двух месяцев", 21: "Есть ли все доступы?"}
        rep2 = await store.import_xlsx(s, XLSX)
        assert (rep2.created, rep2.updated, rep2.deactivated) == (0, 68, 0)


def test_dangling_question_removed_only_without_buttons():
    gym = "На территории есть спортивный комплекс. Рассказать подробнее о расписании и оснащении?"
    assert store._drop_dangling_question(gym, False) == "На территории есть спортивный комплекс."
    assert store._drop_dangling_question(gym, True) == gym
    assert store._drop_dangling_question("Я собрал всю информацию. Интересно?", False) == "Я собрал всю информацию."
    assert store._drop_dangling_question("Ты готов?", False) == "Ты готов?"
