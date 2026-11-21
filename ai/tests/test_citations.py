"""``[n]`` citation parsing and cleaning."""

from __future__ import annotations

from ai_service.agents.citations import (
    clean_citations,
    extract_citation_numbers,
    is_not_covered,
    strip_thinking,
)


def test_extracts_adjacent_and_comma_separated_markers() -> None:
    assert extract_citation_numbers("A [1][2]. B [2, 3]. C [1,4].") == [1, 2, 3, 4]


def test_ignores_text_that_only_looks_like_a_marker() -> None:
    assert extract_citation_numbers("See [a], [], [1a] and (2).") == []


def test_clean_removes_markers_that_point_at_no_source() -> None:
    text, used = clean_citations("Rest helps [1]. Magic cure [9].", {1, 2})

    assert text == "Rest helps [1]. Magic cure."
    assert used == [1]


def test_clean_keeps_the_valid_part_of_a_mixed_marker() -> None:
    text, used = clean_citations("Fluids help [1, 7].", {1})

    assert text == "Fluids help [1]."
    assert used == [1]


def test_clean_with_no_valid_citation_reports_none() -> None:
    _, used = clean_citations("Unsourced claim [5].", {1})

    assert used == []


def test_sentinel_detection() -> None:
    assert is_not_covered("NOT_IN_DOCUMENTS")
    assert is_not_covered("  NOT_IN_DOCUMENTS \n")
    assert is_not_covered("**NOT_IN_DOCUMENTS**")
    assert not is_not_covered("The documents say NOT_IN_DOCUMENTS applies here [1].")


def test_strip_thinking_removes_the_block() -> None:
    assert strip_thinking("<think>\n\n</think>\n\nAnswer [1].") == "Answer [1]."
    assert strip_thinking("Answer [1].") == "Answer [1]."
