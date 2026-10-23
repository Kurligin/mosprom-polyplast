"""Гибридный поиск по базе знаний: полнотекстовый (tsvector) + векторный (bge-m3, HNSW) → RRF.

Фильтр по площадке новичка: у документа пустой site_codes — для всех площадок;
ответ HR — только подтверждённый (status == "confirmed") и для его площадки или для всех.
Строки длинных пунктов («pvtr:6.4.2/5») сворачиваются в родительский пункт: в ответе — пункт целиком,
а в focus — строка, которая совпала (для выдержки в карточке).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import and_, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HrAnswer, KbChunk, KbDocument
from app.kb.query import build_tsquery, expand_synonyms

log = logging.getLogger(__name__)

POOL = 30  # кандидатов из каждого поиска до объединения
RRF_K = 60
DRAFT_LABEL = "Черновик HR, ждёт подтверждения"


@dataclass
class Hit:
    chunk_id: int
    text: str
    source_label: str
    score: float  # RRF: порядок выдачи
    is_kt: bool
    status: str  # статус документа или ответа HR: confirmed | draft
    sim: float | None = None  # косинусная близость к вопросу (для порогов)
    fts: float = 0.0  # ts_rank_cd
    fts_rank: int | None = None
    vec_rank: int | None = None
    chunk_key: str | None = None
    title: str = ""  # документ, статья или вопрос ответа HR
    kind: str = "doc"  # doc | scenario | article | hr_answer
    path: str = ""
    point_no: str | None = None
    focus: str | None = None  # совпавшая строка длинного пункта
    members: list[int] = field(default_factory=list, repr=False)

    @property
    def is_draft(self) -> bool:
        return self.status != "confirmed"


def _visible(site_code: str | None):
    """Условие видимости фрагмента для новичка с площадки site_code (None — без фильтра)."""
    doc_ok = KbChunk.document_id.isnot(None)
    hr_ok = and_(KbChunk.hr_answer_id.isnot(None), HrAnswer.status == "confirmed")
    if site_code:
        doc_ok = and_(doc_ok, or_(func.jsonb_array_length(KbDocument.site_codes) == 0,
                                  KbDocument.site_codes.contains([site_code])))
        hr_ok = and_(hr_ok, or_(HrAnswer.site_code.is_(None), HrAnswer.site_code == site_code))
    return or_(doc_ok, hr_ok)


def _base(*cols):
    return (select(*cols)
            .outerjoin(KbDocument, KbDocument.id == KbChunk.document_id)
            .outerjoin(HrAnswer, HrAnswer.id == KbChunk.hr_answer_id))


async def fts_ranked(session: AsyncSession, question: str, site_code: str | None, pool: int = POOL,
                     norm: int = 0) -> list[tuple[int, float]]:
    tsq = build_tsquery(question)
    if not tsq:
        return []
    q = func.to_tsquery("russian", tsq)
    rank = func.ts_rank_cd(KbChunk.tsv, q, norm).label("r")
    stmt = (_base(KbChunk.id, rank).where(KbChunk.tsv.op("@@")(q), _visible(site_code))
            .order_by(rank.desc(), KbChunk.id).limit(pool))
    return [(r.id, float(r.r)) for r in (await session.execute(stmt)).all()]


async def vec_ranked(session: AsyncSession, qvec: list[float], site_code: str | None,
                     pool: int = POOL) -> list[tuple[int, float]]:
    # больше кандидатов в обходе графа HNSW: фильтр по площадке отсекает часть найденного
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    dist = KbChunk.embedding.cosine_distance(qvec)
    stmt = (_base(KbChunk.id, (literal(1.0) - dist).label("sim"))
            .where(KbChunk.embedding.isnot(None), _visible(site_code))
            .order_by(dist).limit(pool))
    return [(r.id, float(r.sim)) for r in (await session.execute(stmt)).all()]


def _parent_key(key: str | None) -> str | None:
    return key.split("/", 1)[0] if key and "/" in key else None


async def _details(session: AsyncSession, ids: list[int], qvec: list[float] | None) -> dict[int, dict]:
    if not ids:
        return {}
    cols = [KbChunk.id, KbChunk.text, KbChunk.source_label, KbChunk.is_kt, KbChunk.chunk_key, KbChunk.path,
            KbChunk.point_no, KbChunk.document_id, KbChunk.hr_answer_id, KbDocument.status.label("doc_status"),
            KbDocument.title.label("doc_title"), KbDocument.doc_type, HrAnswer.status.label("hr_status")]
    if qvec is not None:
        cols.append((literal(1.0) - KbChunk.embedding.cosine_distance(qvec)).label("sim"))
    rows = (await session.execute(_base(*cols).where(KbChunk.id.in_(ids)))).all()
    return {r.id: r._asdict() for r in rows}


async def _parents(session: AsyncSession, subs: list[dict], qvec: list[float] | None) -> dict[tuple, dict]:
    """Родительские пункты для строк длинных пунктов: (document_id, key) → строка."""
    want = {(d["document_id"], _parent_key(d["chunk_key"])) for d in subs}
    if not want:
        return {}
    stmt = select(KbChunk.id).where(KbChunk.document_id.in_({w[0] for w in want}),
                                    KbChunk.chunk_key.in_({w[1] for w in want}))
    ids = [r for r in (await session.execute(stmt)).scalars().all()]
    det = await _details(session, ids, qvec)
    return {(d["document_id"], d["chunk_key"]): d for d in det.values()}


def _kind(d: dict) -> str:
    if d["hr_answer_id"]:
        return "hr_answer"
    if d["doc_type"] in ("scenario", "article"):
        return d["doc_type"]
    return "doc"


def _label(d: dict, status: str) -> str:
    if status != "confirmed" and not d["hr_answer_id"]:
        return DRAFT_LABEL
    return d["source_label"] or d["doc_title"] or "База знаний"


async def search(session: AsyncSession, query: str, *, site_code: str | None, limit: int = 5,
                 pool: int = POOL, qvec: list[float] | None = None, fts_norm: int = 0,
                 vec_weight: float = 1.0, fts_weight: float = 1.0) -> list[Hit]:
    """Топ фрагментов для вопроса: [Hit] по убыванию score (RRF).

    qvec можно передать заранее (оценка на эталоне); иначе считаем эмбеддинг вопроса.
    Если модель эмбеддингов недоступна — только полнотекстовый поиск.
    """
    query = (query or "").strip()
    if not query:
        return []
    if qvec is None:
        try:
            from app.kb.embed import embed_one

            qvec = await embed_one(query)
        except Exception:  # без векторов отвечаем по полнотексту
            log.exception("Эмбеддинг вопроса не посчитан, поиск только по тексту")
            qvec = None

    fts = await fts_ranked(session, expand_synonyms(query), site_code, pool, fts_norm)
    vec = await vec_ranked(session, qvec, site_code, pool) if qvec is not None else []

    scores: dict[int, float] = {}
    for w, lst in ((fts_weight, fts), (vec_weight, vec)):
        for rank, (cid, _) in enumerate(lst, start=1):
            scores[cid] = scores.get(cid, 0.0) + w / (RRF_K + rank)
    fts_pos = {cid: (i, r) for i, (cid, r) in enumerate(fts, start=1)}
    vec_pos = {cid: i for i, (cid, _) in enumerate(vec, start=1)}

    det = await _details(session, list(scores), qvec)
    subs = [d for d in det.values() if _parent_key(d["chunk_key"])]
    parents = await _parents(session, subs, qvec)

    hits: dict[int, Hit] = {}
    sub_best: dict[int, float] = {}
    for cid, sc in scores.items():
        d = det.get(cid)
        if d is None:
            continue
        focus = None
        pkey = _parent_key(d["chunk_key"])
        if pkey:
            p = parents.get((d["document_id"], pkey))
            if p is None:
                continue
            focus, d_sim, d = d["text"], d.get("sim"), p
        else:
            d_sim = d.get("sim")
        status = d["hr_status"] if d["hr_answer_id"] else (d["doc_status"] or "confirmed")
        h = hits.get(d["id"])
        if h is None:
            h = Hit(chunk_id=d["id"], text=d["text"], source_label=_label(d, status), score=0.0, is_kt=d["is_kt"],
                    status=status, sim=None, chunk_key=d["chunk_key"], path=d["path"] or "",
                    title=d["doc_title"] or d["path"] or "", kind=_kind(d), point_no=d["point_no"])
            hits[d["id"]] = h
        h.score = max(h.score, sc)
        if focus is not None and sc > sub_best.get(h.chunk_id, 0.0):
            sub_best[h.chunk_id] = sc
            h.focus = focus
        if d_sim is not None and (h.sim is None or d_sim > h.sim):
            h.sim = float(d_sim)
        fp = fts_pos.get(cid)
        if fp and (h.fts_rank is None or fp[0] < h.fts_rank):
            h.fts_rank, h.fts = fp[0], fp[1]
        vp = vec_pos.get(cid)
        if vp and (h.vec_rank is None or vp < h.vec_rank):
            h.vec_rank = vp
        h.members.append(cid)

    out = sorted(hits.values(), key=lambda h: (-h.score, -(h.sim or 0.0)))
    return out[:limit]
