"""Admin document routes (CLAUDE.md §9, §17 task 2.9). Every route requires the admin."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.deps import AdminUser, DbSession
from app.models import Document
from app.schemas.document import DocumentResponse
from app.services import ai_client, document_service
from app.services.ai_client import AiServiceError
from app.services.document_service import UploadRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/documents", tags=["admin"])

MAX_ERROR_CHARS = 2000
AI_UNAVAILABLE_ON_DELETE = (
    "The AI service could not remove this document from the search index, so nothing was "
    "deleted. Make sure the AI service is running and try again."
)


def _load_job(doc_id: int, settings: Settings) -> tuple[str, str] | None:
    """The absolute path and display name to ingest, or ``None`` if the row is gone."""
    with get_sessionmaker()() as db:
        document = db.get(Document, doc_id)
        if document is None:
            return None
        return str(document_service.absolute_path(settings, document)), document.filename


def _finish_job(doc_id: int, *, chunk_count: int = 0, error: str | None = None) -> None:
    with get_sessionmaker()() as db:
        document = db.get(Document, doc_id)
        if document is None:  # deleted while ingesting
            return
        if error is None:
            document.status, document.chunk_count, document.error = "ready", chunk_count, None
        else:
            document.status, document.error = "failed", error[:MAX_ERROR_CHARS]
        db.commit()


async def run_ingest(doc_id: int) -> None:
    """Background task: ask the AI service to index the file, then record the outcome.

    Never raises - a failure is stored on the row as ``status="failed"`` plus ``error``.
    """
    settings = get_settings()
    try:
        job = await run_in_threadpool(_load_job, doc_id, settings)
        if job is None:
            return
        file_path, filename = job
        result = await ai_client.ingest_document(settings, doc_id, file_path, filename)
        await run_in_threadpool(_finish_job, doc_id, chunk_count=result.chunk_count)
        logger.info("document %d ready chunks=%d", doc_id, result.chunk_count)
    except AiServiceError as exc:
        logger.warning("ingest of document %d failed: %s", doc_id, exc)
        await run_in_threadpool(_finish_job, doc_id, error=str(exc))
    except Exception as exc:
        logger.exception("unexpected error ingesting document %d", doc_id)
        await run_in_threadpool(_finish_job, doc_id, error=f"unexpected error: {exc}")


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document (ingested in the background)",
)
def upload_document(
    file: UploadFile,
    background: BackgroundTasks,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    admin: AdminUser,
) -> Document:
    try:
        document = document_service.save_upload(
            db, settings, file.file, file.filename or "", admin.id
        )
    except UploadRejected as exc:
        raise HTTPException(exc.status_code, detail=exc.detail) from exc
    background.add_task(run_ingest, document.id)
    return document


@router.get("", response_model=list[DocumentResponse], summary="List documents with status")
def list_documents(db: DbSession, _admin: AdminUser) -> list[Document]:
    statement = select(Document).order_by(Document.created_at.desc(), Document.id.desc())
    return list(db.scalars(statement))


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document, its file and its vectors",
)
async def delete_document(
    document_id: int,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    _admin: AdminUser,
) -> None:
    document = await run_in_threadpool(db.get, Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
    if document.status == "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="this document is still being processed - try again in a moment",
        )

    # The index goes first: if the AI service is down nothing is removed, so the row never
    # outlives vectors we could no longer clean up.
    try:
        await ai_client.delete_document_index(settings, document_id)
    except AiServiceError as exc:
        logger.warning("could not delete document %d from the index: %s", document_id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE_ON_DELETE
        ) from exc

    def remove() -> None:
        document_service.delete_document_file(settings, document)
        db.delete(document)
        db.commit()

    await run_in_threadpool(remove)
    logger.info("deleted document %d", document_id)
