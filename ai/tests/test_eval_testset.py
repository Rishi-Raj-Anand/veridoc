"""The hand-written evaluation test set: format and validation (CLAUDE.md §13, 7.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_service.eval.testset import (
    TestSetError,
    check_sources,
    load_testset,
    parse_testset,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "ai_service" / "eval" / "testset.example.jsonl"


def row(**fields: object) -> str:
    base: dict[str, object] = {
        "question": "What is malaria?",
        "reference": "A parasite.",
        "source": "disease.md",
    }
    base.update(fields)
    return json.dumps({k: v for k, v in base.items() if v is not ...})


def test_the_shipped_example_is_valid() -> None:
    cases = load_testset(EXAMPLE)

    assert [c.expect for c in cases] == ["answer", "answer", "fallback"]
    assert cases[2].source is None


def test_rows_get_line_ids_and_comments_and_blank_lines_are_skipped() -> None:
    cases = parse_testset(["// a comment", "", row(), row(question="What is dengue?", id="d1")])

    assert [c.id for c in cases] == ["line-3", "d1"]
    assert cases[0].expect == "answer"


def test_every_problem_is_reported_with_its_line_number() -> None:
    lines = [
        row(),
        "{not json",
        row(reference=""),
        row(colour="red"),
        row(question="What is dengue?", source=...),
        row(),  # duplicate of line 1
    ]

    with pytest.raises(TestSetError) as err:
        parse_testset(lines)

    message = str(err.value)
    assert "line 2: not valid JSON" in message
    assert "line 3: reference" in message
    assert "line 4: colour" in message
    assert "line 5" in message and "'source' is required" in message
    assert "line 6: duplicate question (first seen on line 1)" in message


def test_a_duplicate_question_ignores_case_and_spacing() -> None:
    with pytest.raises(TestSetError, match="duplicate question"):
        parse_testset([row(), row(question="  what IS   malaria? ")])


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(TestSetError, match="duplicate id"):
        parse_testset([row(id="a"), row(question="What is dengue?", id="a")])


def test_an_off_corpus_row_may_have_no_source() -> None:
    (case,) = parse_testset([row(source=None, expect="fallback")])

    assert (case.expect, case.source) == ("fallback", None)


def test_an_empty_or_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(TestSetError, match="no rows"):
        parse_testset(["// only a comment"])
    with pytest.raises(TestSetError, match=r"testset\.example\.jsonl"):
        load_testset(tmp_path / "missing.jsonl")


def test_sources_must_be_indexed_documents() -> None:
    cases = parse_testset(
        [
            row(),
            row(question="Q2?", source="2_disease.md"),
            row(question="Q3?", source=None, expect="fallback"),
        ]
    )

    check_sources(cases[:1], {"disease.md"})
    with pytest.raises(TestSetError, match=r"not in the knowledge base: 2_disease\.md"):
        check_sources(cases, {"disease.md"})
    with pytest.raises(TestSetError, match="the knowledge base is empty"):
        check_sources(cases, set())
