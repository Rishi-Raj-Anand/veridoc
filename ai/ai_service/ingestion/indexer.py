"""Ingestion orchestration (CLAUDE.md §6.1): parse -> chunk -> embed -> store.

All embedding happens in one batched pass *before* any write, so the LLM is never swapped
in mid-ingest (§6.8) and a failed embed leaves the previous index of the document intact.
Re-ingesting a ``doc_id`` replaces its data, so the operation is idempotent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ai_service.config import Settings
from ai_service.ingestion.chunker import chunk_document
from ai_service.ingestion.parsers import parse_document
from ai_service.llm.ollama_client import OllamaClient
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    chunk_count: int
    parent_count: int


def index_document(
    doc_id: int,
    file_path: Path,
    filename: str,
    *,
    store: VectorStore,
    docstore: Docstore,
    llm: OllamaClient,
    settings: Settings,
) -> IngestResult:
    started = time.perf_counter()
    pages = parse_document(file_path)
    parsed = time.perf_counter()
    parents, children = chunk_document(doc_id, pages, settings)
    chunked = time.perf_counter()
    vectors = llm.embed([child.text for child in children])
    embedded = time.perf_counter()

    try:
        store.delete_document(doc_id)
        docstore.replace_document(doc_id, filename, parents)
        store.upsert_children(doc_id, filename, children, vectors)
    except Exception:
        # never leave half an index behind
        store.delete_document(doc_id)
        docstore.delete_document(doc_id)
        raise
    done = time.perf_counter()

    logger.info(
        "ingested doc_id=%d pages=%d parents=%d children=%d "
        "parse=%.2fs chunk=%.2fs embed=%.2fs write=%.2fs",
        doc_id,
        len(pages),
        len(parents),
        len(children),
        parsed - started,
        chunked - parsed,
        embedded - chunked,
        done - embedded,
    )
    return IngestResult(chunk_count=len(children), parent_count=len(parents))
