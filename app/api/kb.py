"""База: документы и ответы HR, загрузка PDF / PPTX, подтверждение ответа HR, загрузка сценария xlsx."""

from __future__ import annotations

import dataclasses
import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.auth import HrDep
from app.api.deps import SessionDep, call, need, save_upload
from app.api.views import answer_json, document_json, kb_documents
from app.db.models import HrAnswer, KbChunk, KbDocument, Site

log = logging.getLogger(__name__)

router = APIRouter()

_TRUE = {"1", "true", "yes", "on", "да"}


@router.get("/kb/documents")
async def list_documents(staff: HrDep, session: SessionDep) -> list:
    return await kb_documents(session)


@router.post("/kb/documents")
async def upload_document(file: Annotated[UploadFile, File()], staff: HrDep, session: SessionDep,
                          title: Annotated[str, Form()] = "", is_kt: Annotated[str, Form()] = "false",
                          site_codes: Annotated[str, Form()] = "") -> dict:
    ingest = need("app.kb.ingest", "ingest_file", "Загрузка документов")
    if (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Сценарий xlsx загружается отдельно: «Загрузить сценарий»")
    sites = [c for c in re.split(r"[,\s]+", site_codes or "") if c and c.lower() != "all"]
    for code in sites:
        if await session.get(Site, code) is None:
            raise HTTPException(400, f"Площадки {code} нет")
    path = await save_upload(file, "kb", (".pdf", ".pptx", ".md"))
    title = " ".join(title.split()) or Path(file.filename or path.name).stem
    try:
        doc, n_chunks = await call(ingest, session, path, title=title, is_kt=is_kt.strip().lower() in _TRUE,
                                   site_codes=sites, uploaded_by=staff.id)
    except ValueError as e:
        await session.rollback()
        path.unlink(missing_ok=True)  # непринятый файл на диске не храним
        raise HTTPException(400, f"Не удалось разобрать файл: {e}") from None
    await session.commit()
    return document_json(doc, int(n_chunks))


@router.post("/kb/documents/{doc_id}/confirm")
async def confirm_document(doc_id: int, staff: HrDep, session: SessionDep) -> dict:
    """Черновик документа (например, статья «Доступ к корпоративному порталу») → в поиск бота."""
    doc = await session.get(KbDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "Документ не найден")
    doc.status = "confirmed"
    await session.commit()
    n = await session.scalar(select(func.count()).where(KbChunk.document_id == doc.id)) or 0
    return document_json(doc, int(n))


class ConfirmInput(BaseModel):
    question: str | None = None
    answer: str | None = None


@router.post("/kb/answers/{answer_id}/confirm")
async def confirm_answer(answer_id: int, staff: HrDep, session: SessionDep,
                         body: ConfirmInput | None = None) -> dict:
    index = need("app.kb.ingest", "index_hr_answer", "База знаний")
    ha = await session.get(HrAnswer, answer_id)
    if ha is None:
        raise HTTPException(404, "Ответ HR не найден")
    if body and body.question and body.question.strip():
        ha.question = body.question.strip()
    if body and body.answer and body.answer.strip():
        ha.answer = body.answer.strip()
    ha.status = "confirmed"
    await session.flush()
    await call(index, session, ha)
    await session.commit()
    n = await session.scalar(select(func.count()).where(KbChunk.hr_answer_id == ha.id)) or 0
    return answer_json(ha, int(n))


def _report_dict(report) -> dict:
    if dataclasses.is_dataclass(report):
        return dataclasses.asdict(report)
    if hasattr(report, "model_dump"):
        return report.model_dump()
    if isinstance(report, dict):
        return report
    return dict(vars(report))


@router.post("/scenario/import")
async def import_scenario(file: Annotated[UploadFile, File()], staff: HrDep,
                          session: SessionDep) -> dict:
    importer = need("app.scenario.store", "import_xlsx", "Загрузка сценария")
    path = await save_upload(file, "scenario", (".xlsx",))
    try:
        report = await call(importer, session, path)
    except ValueError as e:
        await session.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Не удалось разобрать сценарий: {e}") from None
    await session.commit()
    r = _report_dict(report)
    return {
        "steps": int(r.get("steps") or 0),
        "by_site": dict(r.get("by_site") or {}),
        "surveys": int(r.get("surveys") or 0),
        "attachments": int(r.get("attachments") or 0),
        "new_sites": list(r.get("new_sites") or []),
        "warnings": [str(w) for w in (r.get("warnings") or [])],
    }
