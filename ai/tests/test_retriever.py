"""Iteration-1 retrieval: Top-3, score gate, grouping by (filename, page)."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from ai_service.config import Settings
from ai_service.ingestion.chunker import ChildChunk
from ai_service.retrieval.retriever import Retriever, format_context, group_sources
from ai_service.retrieval.vector_store import SearchHit, VectorStore
from tests.helpers import FakeLlm

DIM = 1024


def v(*head: float) -> list[float]:
    return list(head) + [0.0] * (DIM - len(head))


def chunk(index: int, page: int, text: str) -> ChildChunk:
    return ChildChunk(parent_id="1:p00000", chunk_index=index, page=page, text=text)


@pytest.fixture
def store() -> VectorStore:
    vector_store = VectorStore(QdrantClient(":memory:"), Settings(_env_file=None))
    vector_store.ensure_collection()
    return vector_store


def retriever(store: VectorStore, **settings: float) -> Retriever:
    config = Settings(_env_file=None, **settings)
    return Retriever(store, FakeLlm(query_vector=v(1.0)), config)


def test_returns_only_the_top_three_chunks(store: VectorStore) -> None:
    children = [chunk(i, i + 1, f"text {i}") for i in range(5)]
    vectors = [v(1.0, 0.1 * i) for i in range(5)]
    store.upsert_children(1, "a.md", children, vectors)

    result = retriever(store).retrieve("question")

    assert [s.text for s in result.sources] == ["text 0", "text 1", "text 2"]


def test_gate_passes_at_or_above_the_minimum_score(store: VectorStore) -> None:
    store.upsert_children(1, "a.md", [chunk(0, 1, "x")], [v(1.0)])

    assert retriever(store, min_retrieval_score=1.0).retrieve("q").passed_gate


def test_gate_fails_below_the_minimum_score(store: VectorStore) -> None:
    store.upsert_children(1, "a.md", [chunk(0, 1, "x")], [v(0.3, 0.95)])

    result = retriever(store, min_retrieval_score=0.35).retrieve("q")

    assert not result.passed_gate
    assert result.best_score == pytest.approx(0.3 / (0.3**2 + 0.95**2) ** 0.5, rel=1e-3)


def test_an_empty_knowledge_base_fails_the_gate(store: VectorStore) -> None:
    result = retriever(store).retrieve("anything")

    assert not result.passed_gate
    assert result.sources == []
    assert result.best_score == 0.0


def hit(score: float, filename: str, page: int, text: str) -> SearchHit:
    return SearchHit(score, 1, filename, page, "1:p00000", 0, text)


def test_chunks_from_the_same_page_share_one_number() -> None:
    sources = group_sources(
        [hit(0.9, "a.md", 1, "first"), hit(0.8, "b.md", 4, "other"), hit(0.7, "a.md", 1, "second")]
    )

    assert [(s.n, s.filename, s.page) for s in sources] == [(1, "a.md", 1), (2, "b.md", 4)]
    assert sources[0].text == "first\n\nsecond"
    assert sources[0].score == 0.9


def test_numbers_follow_best_score_order() -> None:
    sources = group_sources([hit(0.5, "a.md", 1, "low"), hit(0.9, "b.md", 2, "high")])

    assert [(s.n, s.filename) for s in sources] == [(1, "b.md"), (2, "a.md")]


def test_format_context_labels_each_source() -> None:
    sources = group_sources([hit(0.9, "a.md", 3, "Rest and fluids.")])

    assert format_context(sources) == "[1] a.md, p. 3\nRest and fluids."
