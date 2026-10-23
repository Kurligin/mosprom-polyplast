"""Синхронизация config/surveys.yaml → таблицы surveys и survey_questions (upsert).

Вопрос ищется по ключу из YAML (branches["_key"]), поэтому порядок и тексты можно менять,
а ответы остаются привязаны к своему вопросу. Вопросы, которых больше нет в YAML, не удаляем:
на них могут ссылаться ответы.

CLI: python -m app.surveys.store
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import Survey, SurveyQuestion
from app.surveys import config as C

KEY = "_key"

# code → {key → question_id}; сбрасывается, если YAML изменился
_ids: dict = {"mtime": None, "map": {}}


def _branches(q: C.Question) -> dict:
    out: dict = {KEY: q.key, "_step": q.step}
    if q.sites:
        out["_sites"] = q.sites
    if q.legend:
        out["_legend"] = q.legend
    for o in q.options:
        b = {k: v for k, v in {
            "reply": o.reply, "ask": o.ask, "mood": o.mood, "sites": o.sites or None,
            "actions": [a.do for a in o.actions] or None, "exclusive": o.exclusive or None,
        }.items() if v}
        if b:
            out[o.label] = b
    return out


def _options(q: C.Question) -> list:
    if q.kind == "scale":
        return [str(i) for i in range(1, 11)]
    return [o.label for o in q.options]


async def sync(session) -> dict[str, dict[str, int]]:
    """Upsert опросов и вопросов. Возвращает {code: {key: question_id}}. Коммит — на вызывающем."""
    surveys = C.load()
    result: dict[str, dict[str, int]] = {}
    for sv in surveys.values():
        row = await session.scalar(select(Survey).where(Survey.code == sv.code))
        desc = "Шаги сценария: " + ", ".join(map(str, sv.steps)) if sv.steps else None
        if row is None:
            row = Survey(code=sv.code, title=sv.title, description=desc)
            session.add(row)
            await session.flush()
        else:
            row.title, row.description = sv.title, desc
        existing = (await session.scalars(select(SurveyQuestion).where(SurveyQuestion.survey_id == row.id))).all()
        by_key = {(q.branches or {}).get(KEY): q for q in existing}
        ids: dict[str, int] = {}
        for order, q in enumerate(sv.questions, start=1):
            dbq = by_key.get(q.key)
            fields = {
                "order_no": order, "kind": q.kind, "text": q.text, "options": _options(q),
                "reason_threshold": (q.reason_below - 1) if q.reason_below else None,
                "branches": _branches(q),
                "flag_triggers": {o.label: o.flag for o in q.options if o.flag},
                "topic": q.topic,
            }
            if dbq is None:
                dbq = SurveyQuestion(survey_id=row.id, **fields)
                session.add(dbq)
            else:
                for k, v in fields.items():
                    setattr(dbq, k, v)
            await session.flush()
            ids[q.key] = dbq.id
        result[sv.code] = ids
    return result


_lock = asyncio.Lock()


async def question_ids() -> dict[str, dict[str, int]]:
    """{code: {key: question_id}} с кэшем на процесс.

    Первый вызов (или правка YAML) синхронизирует таблицы в своей сессии и коммитит сразу:
    так id вопросов не пропадут, если транзакция обработчика откатится.
    """
    from app.db.session import async_session

    C.load()
    mtime = C._cache["mtime"]
    if _ids["mtime"] == mtime and _ids["map"]:
        return _ids["map"]
    async with _lock:
        if _ids["mtime"] != mtime or not _ids["map"]:
            async with async_session() as s:
                ids = await sync(s)
                await s.commit()
            _ids.update(map=ids, mtime=mtime)
    return _ids["map"]


def reset_cache() -> None:
    _ids.update(mtime=None, map={})


async def _main() -> None:
    from app.db.session import async_session, engine

    async with async_session() as s:
        ids = await sync(s)
        await s.commit()
    for code, qs in ids.items():
        print(f"{code}: {len(qs)} вопросов")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
