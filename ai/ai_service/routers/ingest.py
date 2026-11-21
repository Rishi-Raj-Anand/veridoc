"""Ingestion endpoints (CLAUDE.md §9): ``POST /internal/ingest`` and document delete.

Blocking work (parsing, Qdrant local calls, embedding) runs in ``asyncio.to_thread``.
Ingest and delete are serialised by one lock: Qdrant local mode is single-process, and
two concurrent ingests would only fight over VRAM.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ai_service.config import Settings, get_settings
from ai_service.deps import DocstoreDep, LlmDep, VectorStoreDep
from ai_service.ingestion.indexer import index_document
from ai_service.ingestion.parsers import ParseError
from ai_service.llm.ollama_client import OllamaError
from ai_service.security import require_internal_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["ingest"],
    dependencies=[Depends(require_internal_key)],
)


class IngestRequest(BaseModel):
    doc_id: int
    file_path: str
    filename: str


class IngestResponse(BaseModel):
    chunk_count: int


class DeleteResponse(BaseModel):
    deleted_points: int
    deleted_parents: int


def resolve_upload_path(settings: Settings, file_path: str) -> Path:
    """Only files inside ``data/uploads`` may be ingested; never trust a caller's path."""
    resolved = Path(file_path).expanduser().resolve()
    if not resolved.is_relative_to(settings.uploads_dir):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="file_path must be inside the uploads directory"
        )
    if not resolved.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="uploaded file not found")
    return resolved


def _lock(request: Request) -> asyncio.Lock:
    lock = getattr(request.app.state, "ingest_lock", None)
    if lock is None:
        lock = request.app.state.ingest_lock = asyncio.Lock()
    return lock


@router.post("/ingest", response_model=IngestResponse, summary="Parse, chunk, embed and index")
async def ingest(
    body: IngestRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: VectorStoreDep,
    docstore: DocstoreDep,
    llm: LlmDep,
) -> IngestResponse:
    path = resolve_upload_path(settings, body.file_path)
    try:
        async with _lock(request):
            result = await asyncio.to_thread(
                index_document,
                body.doc_id,
                path,
                body.filename,
                store=store,
                docstore=docstore,
                llm=llm,
                settings=settings,
            )
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ingest failed for doc_id=%d", body.doc_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"ingestion failed: {exc}"
        ) from exc
    return IngestResponse(chunk_count=result.chunk_count)


@router.delete(
    "/documents/{doc_id}", response_model=DeleteResponse, summary="Remove vectors and parents"
)
async def delete_document(
    doc_id: int,
    request: Request,
    store: VectorStoreDep,
    docstore: DocstoreDep,
) -> DeleteResponse:
    async with _lock(request):
        points = await asyncio.to_thread(store.delete_document, doc_id)
        parents = await asyncio.to_thread(docstore.delete_document, doc_id)
    logger.info("deleted doc_id=%d points=%d parents=%d", doc_id, points, parents)
    return DeleteResponse(deleted_points=points, deleted_parents=parents)
