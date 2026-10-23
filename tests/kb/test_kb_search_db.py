"""Поиск и индексация на живом Postgres: фильтр по площадке, ответы HR только подтверждённые, новая версия.

Всё в транзакции с откатом; эмбеддинги подменены детерминированными векторами (модель не грузим).
"""

import hashlib
import math

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models import HrAnswer, KbChunk, KbDocument
from app.kb import embed as E
from app.kb import ingest
from app.kb.search import search

DIM = 1024


def fake_vec(text: str) -> list[float]:
    v = [0.0] * DIM
    for w in text.lower().split():
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest_asyncio.fixture
async def session(monkeypatch):
    async def fake_embed(texts, batch_size=16):
        return [fake_vec(t) for t in texts]

    monkeypatch.setattr(E, "embed", fake_embed)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres недоступен: {e}")
    trans = await conn.begin()
    s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield s
    finally:
        await s.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _doc(s, title, text, sites, status="confirmed"):
    d = KbDocument(title=title, doc_type="md", source=f"test:{title}", site_codes=sites, status=status)
    s.add(d)
    await s.flush()
    c = KbChunk(document_id=d.id, path=title, text=text, chunk_key=f"test:{d.id}", source_label=title,
                embedding=fake_vec(text))
    s.add(c)
    await s.flush()
    return c


@pytest.mark.asyncio
async def test_site_filter_and_hr_status(session):
    q = "зюзябрик парковка квантовая"
    all_sites = await _doc(session, "Тест: для всех", "зюзябрик парковка квантовая для всех", [])
    gtp = await _doc(session, "Тест: только GTP", "зюзябрик парковка квантовая только завод", ["GTP"])
    hr_ok = HrAnswer(question="зюзябрик парковка квантовая?", answer="Парковка у входа Б (синтетика).",
                     site_code="GrPP", status="confirmed")
    hr_draft = HrAnswer(question="зюзябрик парковка квантовая?", answer="Черновик ответа (синтетика).",
                        status="draft")
    session.add_all([hr_ok, hr_draft])
    await session.flush()
    c_ok = await ingest.index_hr_answer(session, hr_ok)
    c_draft = await ingest.index_hr_answer(session, hr_draft)
    assert c_ok.source_label.startswith("Ответ HR") and c_ok.chunk_key == f"hr:{hr_ok.id}"

    ids_grpp = {h.chunk_id for h in await search(session, q, site_code="GrPP", limit=50, qvec=fake_vec(q))}
    assert all_sites.id in ids_grpp and c_ok.id in ids_grpp
    assert gtp.id not in ids_grpp and c_draft.id not in ids_grpp

    ids_gtp = {h.chunk_id for h in await search(session, q, site_code="GTP", limit=50, qvec=fake_vec(q))}
    assert gtp.id in ids_gtp and all_sites.id in ids_gtp and c_ok.id not in ids_gtp

    # HR подтвердил черновик → ответ появляется в поиске
    hr_draft.status = "confirmed"
    await session.flush()
    hits = await search(session, q, site_code="GTP", limit=50, qvec=fake_vec(q))
    h = next(h for h in hits if h.chunk_id == c_draft.id)
    assert h.kind == "hr_answer" and h.status == "confirmed" and h.fts_rank is not None


@pytest.mark.asyncio
async def test_draft_document_label(session):
    q = "кваквазябра портал доступ"
    c = await _doc(session, "Тест: черновик", "кваквазябра портал доступ шаги", [], status="draft")
    hit = next(h for h in await search(session, q, site_code=None, limit=50, qvec=fake_vec(q)) if h.chunk_id == c.id)
    assert hit.is_draft and hit.source_label == "Черновик HR, ждёт подтверждения"


@pytest.mark.asyncio
async def test_ingest_file_new_version_replaces_old(session, tmp_path):
    p = tmp_path / "memo.md"
    p.write_text("## Слайд 1. Пропуск\n\nПропуск выдают на проходной (синтетика).\n", encoding="utf-8")
    doc, n = await ingest.ingest_file(session, p, title="Тест: памятка", site_codes=["GTP"])
    assert n == 1 and doc.version == 1 and doc.site_codes == ["GTP"]
    p.write_text("## Слайд 1. Пропуск\n\nПропуск выдают в бюро пропусков.\n\n## Слайд 2. Обед\n\nОбед в 13:00.\n",
                 encoding="utf-8")
    doc2, n2 = await ingest.ingest_file(session, p, title="Тест: памятка", is_kt=True)
    assert doc2.id == doc.id and doc2.version == 2 and n2 == 2 and doc2.is_kt
    rows = (await session.execute(select(KbChunk).where(KbChunk.document_id == doc.id))).scalars().all()
    assert sorted(r.chunk_key for r in rows) == [f"doc{doc.id}:1", f"doc{doc.id}:2"]
    assert all(r.is_kt and r.embedding is not None for r in rows)
    assert not any("проходной" in r.text for r in rows)  # старая версия удалена
