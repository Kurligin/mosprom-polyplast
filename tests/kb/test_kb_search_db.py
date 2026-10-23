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


# --- загрузка файлов HR, черновик и подтверждение -----------------------------------------------


def _count_embeds(monkeypatch) -> list[int]:
    """Сколько текстов ушло в эмбеддинг за каждый вызов (поверх фейковой модели фикстуры)."""
    calls: list[int] = []
    inner = E.embed

    async def counting(texts, batch_size=16):
        calls.append(len(texts))
        return await inner(texts, batch_size)

    monkeypatch.setattr(E, "embed", counting)
    return calls


@pytest.mark.asyncio
async def test_ingest_pdf_same_file_again_keeps_version_and_vectors(session, tmp_path, monkeypatch):
    from tests.kb.test_kb_ingest import _pdf_with_text

    p = tmp_path / "rules.pdf"
    _pdf_with_text(p, ["1. GENERAL", "1.1. Work starts at 9:00.", "1.2. Lunch break is 48 minutes."])
    calls = _count_embeds(monkeypatch)
    doc, n = await ingest.ingest_file(session, p, title="  Тест:   правила PDF ", uploaded_by=None)
    assert (doc.title, doc.doc_type, doc.version, n) == ("Тест: правила PDF", "pdf", 1, 2)
    rows = (await session.execute(select(KbChunk).where(KbChunk.document_id == doc.id)
                                  .order_by(KbChunk.chunk_key))).scalars().all()
    assert [(r.chunk_key, r.source_label) for r in rows] == [
        (f"doc{doc.id}:1.1", "Тест: правила PDF, п. 1.1, стр. 1"),
        (f"doc{doc.id}:1.2", "Тест: правила PDF, п. 1.2, стр. 1")]
    ids = [r.id for r in rows]

    doc2, _ = await ingest.ingest_file(session, p, title="Тест: правила PDF")
    assert doc2.id == doc.id and doc2.version == 1, "тот же файл — версия не растёт"
    assert calls == [2, 0], "векторы неизменённых пунктов не пересчитываются"
    rows2 = (await session.execute(select(KbChunk.id).where(KbChunk.document_id == doc.id)
                                   .order_by(KbChunk.chunk_key))).scalars().all()
    assert rows2 == ids, "id фрагментов сохраняются: ссылки из questions_log не теряются"


@pytest.mark.asyncio
async def test_ingest_pptx_skips_empty_slides(session, tmp_path):
    from tests.kb.test_kb_ingest import _pptx

    p = _pptx(tmp_path / "memo.pptx", [("", ""), ("Пропуск", "Пропуск выдают в бюро пропусков (синтетика)."),
                                      ("Обед", "Обед в 13:00 (синтетика).")])
    doc, n = await ingest.ingest_file(session, p, title="Тест: памятка PPTX", site_codes=["GTP"])
    assert doc.doc_type == "pptx" and n == 2
    labels = (await session.execute(select(KbChunk.source_label).where(KbChunk.document_id == doc.id)
                                    .order_by(KbChunk.page))).scalars().all()
    assert labels == ["Тест: памятка PPTX, слайд 2", "Тест: памятка PPTX, слайд 3"]


@pytest.mark.asyncio
async def test_draft_article_stays_confirmed_after_reimport(session):
    """Черновик статьи HR: новая — draft; HR подтвердил — повторный импорт статус не сбрасывает."""
    art = ingest.Article(slug=f"test-draft-{id(session)}", title="Тест: доступ к порталу",
                         text="Логин выдаёт IT в первый день (синтетика).", source="синтетика", kind="Статья HR")
    doc, n = await ingest.ingest_article(session, art, status=None, doc_type="article")
    assert (doc.status, doc.version, n) == ("draft", 1, 1)
    chunk = (await session.execute(select(KbChunk).where(KbChunk.document_id == doc.id))).scalars().one()
    assert chunk.source_label == "Статья HR «Тест: доступ к порталу»" and chunk.path == "Тест: доступ к порталу"

    doc.status = "confirmed"  # кнопка «Подтвердить» в панели
    await session.flush()
    again, _ = await ingest.ingest_article(session, art, status=None, doc_type="article")
    assert again.id == doc.id and again.status == "confirmed" and again.version == 1

    art.text = "Логин и пароль выдаёт IT в первый день (синтетика)."
    again, _ = await ingest.ingest_article(session, art, status=None, doc_type="article")
    assert again.status == "confirmed" and again.version == 2, "новый текст — новая версия"


@pytest.mark.asyncio
async def test_index_new_hr_answer_and_reindex(session):
    ha = HrAnswer(question="  Где   взять спецовку? ", answer="На складе СИЗ (синтетика).", status="draft")
    chunk = await ingest.index_hr_answer(session, ha)  # ещё без id: сохраняется сам
    assert ha.id is not None and chunk.hr_answer_id == ha.id
    assert (chunk.path, chunk.chunk_key, chunk.is_kt) == ("Где взять спецовку?", f"hr:{ha.id}", False)
    first_vec = list(chunk.embedding)

    ha.answer = "На складе СИЗ, корпус 2 (синтетика)."
    again = await ingest.index_hr_answer(session, ha)
    assert again.id == chunk.id and again.text == ha.answer, "тот же фрагмент, без дубля"
    assert list(again.embedding) != first_vec
    n = (await session.execute(select(KbChunk.id).where(KbChunk.hr_answer_id == ha.id))).scalars().all()
    assert len(n) == 1


@pytest.mark.asyncio
async def test_ingest_all_documents_articles_draft(session, tmp_path, monkeypatch):
    """`make kb`: документы из docs/, статьи сценария, черновик HR; статьи, которых нет в сценарии, удаляются.

    Всё синтетическое: свой slug документа, свой тип «статей сценария» — живые строки базы тест не трогает.
    """
    from app.kb import chunker

    slug = "synthetic-memo"
    monkeypatch.setitem(chunker.DOCS, slug, ("Памятка", "slides", False))
    monkeypatch.setitem(ingest.DOC_TITLES, slug, "Тест: памятка новичка")
    (tmp_path / f"{slug}.md").write_text("## Слайд 1. Пропуск\n\nПропуск выдают на проходной (синтетика).\n",
                                         encoding="utf-8")
    kind = "scenario_test"
    monkeypatch.setattr(ingest, "SCENARIO_TYPE", kind)
    art = ingest.Article(slug="test-food", title="Тест: где пообедать", text="Столовая на 1 этаже (синтетика).",
                         site_codes=["GTP"], source="синтетика")
    monkeypatch.setattr(ingest, "load_scenario_articles", lambda path: [art])
    draft = ingest.Article(slug="test-portal", title="Тест: портал", text="Шаги входа (синтетика).",
                           source="синтетика", kind="Статья HR")
    monkeypatch.setattr(ingest, "DRAFT_PORTAL", draft)
    stale = KbDocument(title="Тест: старая статья", doc_type=kind, source="scenario:old | синтетика", status="confirmed")
    session.add(stale)
    await session.flush()

    rep = await ingest.ingest_all(session, docs_dir=tmp_path, scenario_xlsx=tmp_path / "none.xlsx")
    assert rep == {"documents": {"Тест: памятка новичка": 1}, "articles": ["Тест: где пообедать (GTP)"],
                   "draft": "Тест: портал"}
    assert await session.get(KbDocument, stale.id) is None, "статьи, которой нет в сценарии, больше нет"
    memo = (await session.execute(select(KbDocument).where(KbDocument.source == f"docs/{slug}.md"))).scalars().one()
    assert memo.doc_type == "md" and memo.status == "confirmed" and memo.site_codes == []
    portal = (await session.execute(select(KbDocument).where(KbDocument.source == f"{draft.key} | синтетика"))
              ).scalars().one()
    assert portal.status == "draft" and portal.doc_type == "article"
