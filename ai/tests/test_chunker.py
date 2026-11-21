"""Chunker invariants (CLAUDE.md §6.1.4): sizes, overlap, parent links."""

from __future__ import annotations

import itertools

from ai_service.config import Settings
from ai_service.ingestion.chunker import (
    CHARS_PER_TOKEN,
    chunk_document,
    estimate_tokens,
    make_parent_id,
    split_oversize,
)
from ai_service.ingestion.parsers import PageText


def make_settings() -> Settings:
    return Settings(_env_file=None)


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def paragraph(n_words: int, tag: str) -> str:
    return words(n_words, tag) + "."


def long_pages(page_count: int = 3, paragraphs_per_page: int = 6) -> list[PageText]:
    pages = []
    for p in range(1, page_count + 1):
        paras = [paragraph(60, f"p{p}x{i}x") for i in range(paragraphs_per_page)]
        pages.append(PageText(page=p, text="\n\n".join(paras)))
    return pages


def test_estimate_tokens_is_len_over_four_rounded_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_parent_ids_are_deterministic() -> None:
    assert make_parent_id(7, 3) == "7:p00003"
    parents_a, _ = chunk_document(1, long_pages(), make_settings())
    parents_b, _ = chunk_document(1, long_pages(), make_settings())
    assert [p.parent_id for p in parents_a] == [p.parent_id for p in parents_b]


def test_parent_sizes_respect_the_target() -> None:
    settings = make_settings()
    parents, _ = chunk_document(1, long_pages(), settings)

    assert len(parents) > 1
    ceiling = settings.parent_chunk_tokens * 1.25
    assert all(estimate_tokens(p.text) <= ceiling for p in parents)


def test_children_are_about_the_child_size() -> None:
    settings = make_settings()
    _, children = chunk_document(1, long_pages(), settings)

    assert children
    assert all(estimate_tokens(c.text) <= settings.child_chunk_tokens for c in children)
    # all but each parent's last child fill most of the window
    full = [c for c in children if estimate_tokens(c.text) >= settings.child_chunk_tokens * 0.6]
    assert len(full) >= len(children) // 2


def test_every_child_links_to_a_parent_and_sits_inside_it() -> None:
    parents, children = chunk_document(1, long_pages(), make_settings())
    by_id = {p.parent_id: p for p in parents}

    for child in children:
        assert child.parent_id in by_id
        assert child.text in by_id[child.parent_id].text


def test_children_cover_their_parent() -> None:
    parents, children = chunk_document(1, long_pages(), make_settings())

    for parent in parents:
        siblings = [c for c in children if c.parent_id == parent.parent_id]
        assert siblings
        assert parent.text.startswith(siblings[0].text[:40])
        assert parent.text.endswith(siblings[-1].text[-40:])


def test_sibling_children_overlap() -> None:
    settings = make_settings()
    parents, children = chunk_document(1, long_pages(), settings)
    overlaps = []
    for parent in parents:
        siblings = [c for c in children if c.parent_id == parent.parent_id]
        for first, second in itertools.pairwise(siblings):
            start = parent.text.index(second.text)
            end_of_first = parent.text.index(first.text) + len(first.text)
            overlaps.append(end_of_first - start)

    assert overlaps and all(o > 0 for o in overlaps)
    target = settings.child_chunk_overlap_tokens * CHARS_PER_TOKEN
    average = sum(overlaps) / len(overlaps)
    assert target * 0.5 <= average <= target * 1.5


def test_chunk_index_is_global_and_sequential() -> None:
    _, children = chunk_document(1, long_pages(), make_settings())

    assert [c.chunk_index for c in children] == list(range(len(children)))


def test_page_numbers_follow_the_source_pages() -> None:
    parents, children = chunk_document(1, long_pages(page_count=3), make_settings())

    assert parents[0].page_start == 1
    assert parents[-1].page_end == 3
    assert {c.page for c in children} == {1, 2, 3}
    assert [c.page for c in children] == sorted(c.page for c in children)


def test_parent_spanning_a_page_boundary_reports_both_pages() -> None:
    pages = [PageText(1, paragraph(40, "a")), PageText(2, paragraph(40, "b"))]

    parents, children = chunk_document(1, pages, make_settings())

    assert len(parents) == 1
    assert (parents[0].page_start, parents[0].page_end) == (1, 2)
    assert children[0].page == 1


def test_short_document_is_one_parent_one_child() -> None:
    parents, children = chunk_document(5, [PageText(1, "Short note.")], make_settings())

    assert len(parents) == 1 and len(children) == 1
    assert children[0].parent_id == parents[0].parent_id == "5:p00000"


def test_heading_stays_with_the_text_that_follows() -> None:
    settings = make_settings()
    body = "\n\n".join(paragraph(60, f"b{i}x") for i in range(4))
    pages = [PageText(1, f"{body}\n\nTreatment of Migraine\n\n{paragraph(60, 'c')}")]

    parents, _ = chunk_document(1, pages, settings)

    assert not any(p.text.rstrip().endswith("Treatment of Migraine") for p in parents)
    assert any("Treatment of Migraine\n\nc0" in p.text for p in parents)


def test_oversize_paragraph_is_split_and_every_piece_fits() -> None:
    settings = make_settings()
    huge = " ".join(f"Sentence number {i} about dosage." for i in range(400))

    parents, _ = chunk_document(1, [PageText(1, huge)], settings)

    assert len(parents) > 1
    assert all(estimate_tokens(p.text) <= settings.parent_chunk_tokens * 1.25 for p in parents)


def test_split_oversize_handles_text_without_spaces_or_sentences() -> None:
    pieces = split_oversize("x" * 2500, 1000)

    assert all(len(p) <= 1000 for p in pieces)
    assert "".join(pieces) == "x" * 2500
