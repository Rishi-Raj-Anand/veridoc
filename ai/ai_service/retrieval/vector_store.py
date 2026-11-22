"""Qdrant local-mode wrapper (CLAUDE.md §4, §6.1.5).

Qdrant local mode allows one process only, so a single ``VectorStore`` wraps the one
embedded client the service opens at startup. Only child chunks are stored.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from ai_service.config import Settings
from ai_service.ingestion.chunker import ChildChunk

COLLECTION = "chunks"
UPSERT_BATCH_SIZE = 256
_POINT_NAMESPACE = uuid.UUID("6f1c5b1e-3b0c-4c7e-9a52-0d6c0f6f4a11")


class VectorStoreError(RuntimeError):
    """The collection is missing or does not match the configured embedding shape."""


@dataclass(frozen=True)
class SearchHit:
    """One child chunk returned by a similarity search (cosine score, higher is closer)."""

    score: float
    doc_id: int
    filename: str
    page: int
    parent_id: str
    chunk_index: int
    text: str


def point_id(doc_id: int, chunk_index: int) -> str:
    """Deterministic point id, so re-ingesting a document overwrites rather than duplicates."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{doc_id}:{chunk_index}"))


def _doc_filter(doc_id: int) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
    )


class VectorStore:
    def __init__(self, client: QdrantClient, settings: Settings) -> None:
        self._client = client
        self._dim = settings.embed_dim
        # Chat searches and ingests run in different worker threads against one embedded
        # client; serialise every call to it.
        self._lock = threading.RLock()

    def ensure_collection(self) -> None:
        """Create ``chunks`` (cosine) if absent; refuse a collection of the wrong size."""
        with self._lock:
            exists = self._client.collection_exists(COLLECTION)
            if not exists:
                self._client.create_collection(
                    COLLECTION,
                    vectors_config=models.VectorParams(
                        size=self._dim, distance=models.Distance.COSINE
                    ),
                )
                return
            params = self._client.get_collection(COLLECTION).config.params.vectors
        size = getattr(params, "size", None)
        distance = getattr(params, "distance", None)
        if size != self._dim or distance != models.Distance.COSINE:
            raise VectorStoreError(
                f"collection '{COLLECTION}' has size={size} distance={distance} but the config "
                f"expects size={self._dim} cosine. Changing the embedding model or EMBED_DIM "
                "needs a full re-index: delete data/vectors/ and data/docstore.db, then "
                "re-upload the documents."
            )

    def upsert_children(
        self,
        doc_id: int,
        filename: str,
        children: Sequence[ChildChunk],
        vectors: Sequence[Sequence[float]],
    ) -> int:
        if len(children) != len(vectors):
            raise VectorStoreError(f"{len(children)} chunks but {len(vectors)} vectors")
        points = [
            models.PointStruct(
                id=point_id(doc_id, child.chunk_index),
                vector=list(vector),
                payload={
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": child.page,
                    "parent_id": child.parent_id,
                    "chunk_index": child.chunk_index,
                    "text": child.text,
                },
            )
            for child, vector in zip(children, vectors, strict=True)
        ]
        with self._lock:
            for start in range(0, len(points), UPSERT_BATCH_SIZE):
                batch = points[start : start + UPSERT_BATCH_SIZE]
                self._client.upsert(COLLECTION, batch, wait=True)
        return len(points)

    def search(self, vector: Sequence[float], limit: int) -> list[SearchHit]:
        """The ``limit`` child chunks closest to ``vector``, best first."""
        with self._lock:
            response = self._client.query_points(
                COLLECTION, query=list(vector), limit=limit, with_payload=True
            )
        hits: list[SearchHit] = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    score=float(point.score),
                    doc_id=int(payload["doc_id"]),
                    filename=str(payload["filename"]),
                    page=int(payload["page"]),
                    parent_id=str(payload["parent_id"]),
                    chunk_index=int(payload["chunk_index"]),
                    text=str(payload["text"]),
                )
            )
        return hits

    def count(self) -> int:
        """Every child chunk in the collection (0 means an empty knowledge base)."""
        with self._lock:
            return self._client.count(COLLECTION, exact=True).count

    def count_document(self, doc_id: int) -> int:
        with self._lock:
            return self._client.count(
                COLLECTION, count_filter=_doc_filter(doc_id), exact=True
            ).count

    def delete_document(self, doc_id: int) -> int:
        """Remove every point of ``doc_id``; returns how many were there."""
        with self._lock:
            existing = self.count_document(doc_id)
            if existing:
                self._client.delete(
                    COLLECTION,
                    points_selector=models.FilterSelector(filter=_doc_filter(doc_id)),
                    wait=True,
                )
            return existing
