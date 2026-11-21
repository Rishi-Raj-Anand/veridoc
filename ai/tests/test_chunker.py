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
    split_table,
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


# --- Markdown tables ---------------------------------------------------------------------

HEADER = "| Disease | Cause | Symptoms | Prevention | Treatment |"
SEPARATOR = "| --- | --- | --- | --- | --- |"


def table_row(i: int, width: int = 60) -> str:
    return (
        f"| **{i}. Disease{i}** | cause{i} "
        + "c" * width
        + f" | symptoms{i} "
        + "s" * width
        + f" | prevention{i} "
        + "p" * width
        + f" | treatment{i} "
        + "t" * width
        + " |"
    )


def make_table(n_rows: int, width: int = 60) -> str:
    return "\n".join([HEADER, SEPARATOR, *(table_row(i, width) for i in range(1, n_rows + 1))])


def table_children(children: list) -> list:
    return [c for c in children if c.text.count("\n") >= 1 and "| --- |" in c.text]


def test_table_children_never_cut_a_row_and_always_carry_the_header() -> None:
    page = PageText(1, f"### Infectious Diseases\n\n{make_table(20)}")

    _, children = chunk_document(1, [page], make_settings())

    pieces = table_children(children)
    assert len(pieces) > 1
    for child in pieces:
        lines = child.text.split("\n")
        assert HEADER in lines and SEPARATOR in lines
        rows = [line for line in lines if line.startswith("| **")]
        assert rows and all(row.endswith("|") for row in rows)


def test_every_table_row_appears_exactly_once_across_children() -> None:
    page = PageText(1, make_table(25))

    _, children = chunk_document(1, [page], make_settings())

    text = "\n".join(c.text for c in children)
    for i in range(1, 26):
        assert text.count(f"| **{i}. Disease{i}** |") == 1


def test_a_heading_above_a_table_travels_with_every_table_piece() -> None:
    page = PageText(1, f"### Infectious Diseases\n\n{make_table(20)}")

    _, children = chunk_document(1, [page], make_settings())

    pieces = table_children(children)
    assert all(c.text.startswith("### Infectious Diseases\n\n| Disease") for c in pieces)
    # ...and the heading is not also left behind as a tiny child of its own
    assert not any(c.text.strip() == "### Infectious Diseases" for c in children)


def test_table_children_stay_near_the_child_size() -> None:
    settings = make_settings()
    page = PageText(1, make_table(20))

    _, children = chunk_document(1, [page], settings)

    assert all(estimate_tokens(c.text) <= settings.child_chunk_tokens + 20 for c in children)


def test_a_table_larger_than_a_parent_splits_into_valid_tables() -> None:
    settings = make_settings()
    page = PageText(1, make_table(30))

    parents, _ = chunk_document(1, [page], settings)

    assert len(parents) > 1
    for parent in parents:
        assert parent.text.startswith(f"{HEADER}\n{SEPARATOR}")
        assert estimate_tokens(parent.text) <= settings.parent_chunk_tokens * 1.25


def test_prose_around_a_table_is_chunked_as_before() -> None:
    before = paragraph(40, "before")
    after = paragraph(40, "after")
    page = PageText(1, f"{before}\n\n{make_table(3)}\n\n{after}")

    parents, children = chunk_document(1, [page], make_settings())

    assert len(parents) == 1
    assert any("before0" in c.text and "| --- |" not in c.text for c in children)
    assert any("after0" in c.text and "| --- |" not in c.text for c in children)
    assert len(table_children(children)) == 1


def test_a_table_glued_to_the_preceding_line_is_still_detected() -> None:
    page = PageText(1, f"Summary of diseases:\n{make_table(3)}")

    _, children = chunk_document(1, [page], make_settings())

    assert len(table_children(children)) == 1
    assert any(c.text.strip() == "Summary of diseases:" for c in children)


def test_pipe_lines_without_a_separator_are_plain_text() -> None:
    page = PageText(1, "| not | a table |\n| just | pipes |")

    parents, children = chunk_document(1, [page], make_settings())

    assert len(children) == 1
    assert children[0].text == parents[0].text == "| not | a table |\n| just | pipes |"


def test_a_single_oversize_row_is_bounded_not_dropped() -> None:
    settings = make_settings()
    huge = f"| **1. Big** | {'word ' * 600} | x |"
    page = PageText(1, "\n".join([HEADER, SEPARATOR, huge]))

    _, children = chunk_document(1, [page], settings)

    limit = settings.child_chunk_tokens * CHARS_PER_TOKEN + len(HEADER) + len(SEPARATOR) + 10
    assert len(children) > 1
    assert all(len(c.text) <= limit for c in children)
    assert "word" in "".join(c.text for c in children)


def test_split_table_pieces_are_each_header_plus_whole_rows() -> None:
    pieces = split_table(make_table(10), 1500)

    assert len(pieces) > 1
    assert all(p.startswith(f"{HEADER}\n{SEPARATOR}\n| **") for p in pieces)
    rows = [line for p in pieces for line in p.split("\n") if line.startswith("| **")]
    assert rows == [table_row(i) for i in range(1, 11)]


def test_the_heading_is_in_every_parent_when_a_table_spans_several() -> None:
    page = PageText(1, f"### Infectious Diseases\n\n{make_table(30)}")

    parents, children = chunk_document(1, [page], make_settings())

    assert len(parents) > 1
    assert all(p.text.startswith("### Infectious Diseases\n\n| Disease") for p in parents)
    assert all(c.text.startswith("### Infectious Diseases\n\n| Disease") for c in children)
