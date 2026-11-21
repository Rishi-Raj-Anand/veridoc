"""Iteration-1 retrieval: Top-3, score gate, grouping by (filename, page)."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from ai_service.config import Settings
from ai_service.ingestion.chunker import ChildChunk, ParentChunk
from ai_service.retrieval.docstore import Docstore
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


@pytest.fixture
def docstore(tmp_path: Path) -> Docstore:
    return Docstore(tmp_path / "docstore.db")


def retriever(store: VectorStore, docstore: Docstore, **settings: float) -> Retriever:
    config = Settings(_env_file=None, **settings)
    return Retriever(store, docstore, FakeLlm(query_vector=v(1.0)), config)


def test_returns_only_the_top_three_chunks(store: VectorStore, docstore: Docstore) -> None:
    children = [chunk(i, i + 1, f"text {i}") for i in range(5)]
    vectors = [v(1.0, 0.1 * i) for i in range(5)]
    store.upsert_children(1, "a.md", children, vectors)

    result = retriever(store, docstore).retrieve(v(1.0), 1)

    assert [s.text for s in result.sources] == ["text 0", "text 1", "text 2"]


def test_gate_passes_at_or_above_the_minimum_score(store: VectorStore, docstore: Docstore) -> None:
    store.upsert_children(1, "a.md", [chunk(0, 1, "x")], [v(1.0)])

    assert retriever(store, docstore, min_retrieval_score=1.0).retrieve(v(1.0), 1).passed_gate


def test_gate_fails_below_the_minimum_score(store: VectorStore, docstore: Docstore) -> None:
    store.upsert_children(1, "a.md", [chunk(0, 1, "x")], [v(0.3, 0.95)])

    result = retriever(store, docstore, min_retrieval_score=0.35).retrieve(v(1.0), 1)

    assert not result.passed_gate
    assert result.best_score == pytest.approx(0.3 / (0.3**2 + 0.95**2) ** 0.5, rel=1e-3)


def test_an_empty_knowledge_base_fails_the_gate(store: VectorStore, docstore: Docstore) -> None:
    result = retriever(store, docstore).retrieve(v(1.0), 1)

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


# --- escalation levels 2 and 3 (CLAUDE.md §6.3) ---------------------------------------


def seed_parents(store: VectorStore, docstore: Docstore, vectors: list[list[float]], per: int = 2):
    """Children ``i`` belong to parent ``i // per``; parent ``k`` sits on page ``k + 1``."""
    children = [
        ChildChunk(f"1:p{i // per:05d}", i, i // per + 1, f"child {i}") for i in range(len(vectors))
    ]
    store.upsert_children(1, "a.md", children, vectors)
    parents = [
        ParentChunk(f"1:p{k:05d}", k, k + 1, k + 1, f"parent {k} full text")
        for k in range((len(vectors) + per - 1) // per)
    ]
    docstore.replace_document(1, "a.md", parents)


def falling(count: int) -> list[list[float]]:
    return [v(1.0, 0.1 * i) for i in range(count)]


def test_level_two_widens_the_top_eight_children_to_at_most_four_parents(
    store: VectorStore, docstore: Docstore
) -> None:
    seed_parents(store, docstore, falling(12))

    result = retriever(store, docstore).retrieve(v(1.0), 2)

    assert result.mode == "expansion"
    assert [s.text for s in result.sources] == [f"parent {k} full text" for k in range(4)]
    assert [s.n for s in result.sources] == [1, 2, 3, 4]


def test_level_three_takes_the_top_fifteen_children_and_at_most_six_parents(
    store: VectorStore, docstore: Docstore
) -> None:
    seed_parents(store, docstore, falling(20))

    result = retriever(store, docstore).retrieve(v(1.0), 3)

    assert len(result.sources) == 6
    assert result.sources[0].text == "parent 0 full text"


def test_parent_limits_come_from_settings(store: VectorStore, docstore: Docstore) -> None:
    seed_parents(store, docstore, falling(12))

    result = retriever(store, docstore, max_parents_level2=2).retrieve(v(1.0), 2)

    assert len(result.sources) == 2


def test_parents_are_deduplicated_and_ordered_by_best_child_score(
    store: VectorStore, docstore: Docstore
) -> None:
    # parent 0 owns the 2nd and 4th best children, parent 2 the best, parent 1 the 3rd.
    children = [
        ChildChunk("1:p00000", 0, 1, "c0"),
        ChildChunk("1:p00001", 1, 2, "c1"),
        ChildChunk("1:p00002", 2, 3, "c2"),
        ChildChunk("1:p00000", 3, 1, "c3"),
    ]
    store.upsert_children(1, "a.md", children, [v(1.0, 0.3), v(1.0, 0.5), v(1.0, 0.0), v(1.0, 0.7)])
    docstore.replace_document(
        1, "a.md", [ParentChunk(f"1:p{k:05d}", k, k + 1, k + 1, f"parent {k}") for k in range(3)]
    )

    result = retriever(store, docstore).retrieve(v(1.0), 2)

    assert [s.text for s in result.sources] == ["parent 2", "parent 0", "parent 1"]


def test_a_parent_spanning_pages_is_cited_at_its_first_page(
    store: VectorStore, docstore: Docstore
) -> None:
    store.upsert_children(1, "a.pdf", [ChildChunk("1:p00000", 0, 3, "c")], [v(1.0)])
    docstore.replace_document(1, "a.pdf", [ParentChunk("1:p00000", 0, 3, 4, "long parent")])

    source = retriever(store, docstore).retrieve(v(1.0), 2).sources[0]

    assert (source.page, source.page_label) == (3, "3-4")
    assert format_context([source]) == "[1] a.pdf, p. 3-4\nlong parent"


def test_a_parent_missing_from_the_docstore_falls_back_to_its_children(
    store: VectorStore, docstore: Docstore
) -> None:
    store.upsert_children(1, "a.md", [ChildChunk("1:p00000", 0, 2, "child text")], [v(1.0)])

    result = retriever(store, docstore).retrieve(v(1.0), 2)

    assert [(s.page, s.text) for s in result.sources] == [(2, "child text")]


def test_the_gate_judges_the_best_child_score_at_every_level(
    store: VectorStore, docstore: Docstore
) -> None:
    store.upsert_children(1, "a.md", [ChildChunk("1:p00000", 0, 1, "c")], [v(0.3, 0.95)])
    docstore.replace_document(1, "a.md", [ParentChunk("1:p00000", 0, 1, 1, "parent")])

    result = retriever(store, docstore, min_retrieval_score=0.35).retrieve(v(1.0), 2)

    assert not result.passed_gate
    assert result.best_score < 0.35
    assert len(result.sources) == 1  # expansion still returns the parent for the Verifier to judge
