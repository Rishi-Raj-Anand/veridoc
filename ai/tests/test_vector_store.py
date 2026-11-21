"""Qdrant wrapper (CLAUDE.md §6.1.5), against a real in-memory Qdrant client."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient, models

from ai_service.config import Settings
from ai_service.ingestion.chunker import ChildChunk
from ai_service.retrieval.vector_store import COLLECTION, VectorStore, VectorStoreError, point_id

DIM = 1024


def vec(seed: float) -> list[float]:
    return [seed] + [0.0] * (DIM - 1)


def child(index: int, text: str = "some text", parent: str = "1:p00000") -> ChildChunk:
    return ChildChunk(parent_id=parent, chunk_index=index, page=2, text=text)


@pytest.fixture
def store() -> VectorStore:
    vector_store = VectorStore(QdrantClient(":memory:"), Settings(_env_file=None))
    vector_store.ensure_collection()
    return vector_store


def test_collection_is_1024_cosine() -> None:
    client = QdrantClient(":memory:")
    VectorStore(client, Settings(_env_file=None)).ensure_collection()

    params = client.get_collection(COLLECTION).config.params.vectors
    assert params.size == 1024
    assert params.distance == models.Distance.COSINE


def test_ensure_collection_is_idempotent(store: VectorStore) -> None:
    store.ensure_collection()
    store.ensure_collection()


def test_wrong_size_collection_raises_with_reindex_hint() -> None:
    client = QdrantClient(":memory:")
    client.create_collection(
        COLLECTION, vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE)
    )

    with pytest.raises(VectorStoreError, match="re-index"):
        VectorStore(client, Settings(_env_file=None)).ensure_collection()


def test_payload_matches_the_spec(store: VectorStore) -> None:
    store.upsert_children(7, "who.pdf", [child(0, "hello", "7:p00000")], [vec(1.0)])

    points, _ = store._client.scroll(COLLECTION, with_payload=True)

    assert len(points) == 1
    assert points[0].payload == {
        "doc_id": 7,
        "filename": "who.pdf",
        "page": 2,
        "parent_id": "7:p00000",
        "chunk_index": 0,
        "text": "hello",
    }


def test_upsert_is_idempotent_per_document(store: VectorStore) -> None:
    children = [child(0), child(1)]
    store.upsert_children(1, "a.pdf", children, [vec(1.0), vec(2.0)])
    store.upsert_children(1, "a.pdf", children, [vec(1.0), vec(2.0)])

    assert store.count_document(1) == 2


def test_point_ids_are_deterministic_and_distinct() -> None:
    assert point_id(1, 0) == point_id(1, 0)
    assert point_id(1, 0) != point_id(1, 1)
    assert point_id(1, 0) != point_id(2, 0)


def test_delete_removes_only_that_document(store: VectorStore) -> None:
    store.upsert_children(1, "a.pdf", [child(0), child(1)], [vec(1.0), vec(2.0)])
    store.upsert_children(2, "b.pdf", [child(0)], [vec(3.0)])

    deleted = store.delete_document(1)

    assert deleted == 2
    assert store.count_document(1) == 0
    assert store.count_document(2) == 1


def test_delete_of_unknown_document_is_a_noop(store: VectorStore) -> None:
    assert store.delete_document(99) == 0


def test_mismatched_vectors_are_rejected(store: VectorStore) -> None:
    with pytest.raises(VectorStoreError):
        store.upsert_children(1, "a.pdf", [child(0), child(1)], [vec(1.0)])


def test_search_ranks_by_cosine_and_returns_the_payload(store: VectorStore) -> None:
    along = [1.0] + [0.0] * (DIM - 1)
    close = [0.6, 0.8] + [0.0] * (DIM - 2)
    across = [0.0, 1.0] + [0.0] * (DIM - 2)
    store.upsert_children(1, "a.pdf", [child(0, "far"), child(1, "near")], [across, close])
    store.upsert_children(2, "b.pdf", [child(0, "exact")], [along])

    hits = store.search(along, limit=10)

    assert [h.text for h in hits] == ["exact", "near", "far"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.6)
    assert (hits[0].doc_id, hits[0].filename, hits[0].page) == (2, "b.pdf", 2)
    assert hits[1].parent_id == "1:p00000"
    assert hits[1].chunk_index == 1


def test_search_respects_limit_and_handles_an_empty_collection(store: VectorStore) -> None:
    assert store.search(vec(1.0), limit=3) == []

    store.upsert_children(1, "a.pdf", [child(i) for i in range(5)], [vec(1.0)] * 5)

    assert len(store.search(vec(1.0), limit=3)) == 3
