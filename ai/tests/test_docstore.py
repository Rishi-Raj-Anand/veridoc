"""Parent-chunk docstore (CLAUDE.md §6.1.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_service.ingestion.chunker import ParentChunk
from ai_service.retrieval.docstore import Docstore


def parent(doc_id: int, index: int, text: str = "text") -> ParentChunk:
    return ParentChunk(
        parent_id=f"{doc_id}:p{index:05d}",
        index=index,
        page_start=index + 1,
        page_end=index + 2,
        text=text,
    )


@pytest.fixture
def docstore(tmp_path: Path) -> Docstore:
    return Docstore(tmp_path / "nested" / "docstore.db")


def test_creates_its_parent_directory_and_round_trips(docstore: Docstore) -> None:
    docstore.replace_document(1, "who.pdf", [parent(1, 0, "alpha"), parent(1, 1, "beta")])

    stored = docstore.get_parents(["1:p00001"])

    assert len(stored) == 1
    assert stored[0].text == "beta"
    assert (stored[0].doc_id, stored[0].filename) == (1, "who.pdf")
    assert (stored[0].page_start, stored[0].page_end) == (2, 3)


def test_get_parents_preserves_requested_order_and_skips_unknown(docstore: Docstore) -> None:
    docstore.replace_document(1, "a.pdf", [parent(1, i) for i in range(3)])

    ids = ["1:p00002", "missing", "1:p00000"]

    assert [p.parent_id for p in docstore.get_parents(ids)] == ["1:p00002", "1:p00000"]
    assert docstore.get_parents([]) == []


def test_replace_swaps_rather_than_appends(docstore: Docstore) -> None:
    docstore.replace_document(1, "a.pdf", [parent(1, 0), parent(1, 1)])
    docstore.replace_document(1, "a.pdf", [parent(1, 0)])

    assert docstore.count_document(1) == 1


def test_delete_removes_only_that_document(docstore: Docstore) -> None:
    docstore.replace_document(1, "a.pdf", [parent(1, 0), parent(1, 1)])
    docstore.replace_document(2, "b.pdf", [parent(2, 0)])

    assert docstore.delete_document(1) == 2
    assert docstore.count_document(1) == 0
    assert docstore.count_document(2) == 1
    assert docstore.delete_document(1) == 0
