"""The hand-written evaluation test set (CLAUDE.md §13, task 7.1).

``testset.jsonl`` holds one JSON object per line, written by the team from the uploaded
documents (not model-generated)::

    {"question": "...", "reference": "ground-truth answer", "source": "disease.md"}

Optional fields:

- ``id``: a short label for the row; defaults to ``line-<n>``.
- ``expect``: ``"answer"`` (default) or ``"fallback"``. A ``fallback`` row is an off-corpus
  question the pipeline should refuse; its ``source`` may be ``null`` and it is scored as
  "correctly refused?", not with RAGAS.

``source`` is the filename **as indexed** (``disease.md``, not ``2_disease.md``); run
``run_ragas --check`` to validate the file against the knowledge base.

Writing good rows:

- Spread the questions across every uploaded document.
- Mix simple facts (answerable from one chunk, iteration 1) with questions whose answer only
  appears in the wider parent section (iteration 2+).
- Include a few dosage questions whose dose the document states explicitly (§7.3).
- Add about 5 ``expect: fallback`` **health** questions the documents do not cover. They test
  the RAG loop's refusal; an off-topic question ("capital of France?") would only test the
  intent classifier. Either refusal (``fallback`` or ``out_of_scope``) counts as correct.
- Blank lines and lines starting with ``//`` are ignored.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_QUESTION_CHARS = 2000  # the same cap as a chat message (§9)


class TestSetError(ValueError):
    """The test set is malformed; the message names every problem with its line number."""

    __test__ = False  # not a pytest test class, despite the name


class TestCase(BaseModel):
    __test__ = False  # not a pytest test class, despite the name

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    reference: str = Field(min_length=1)
    source: str | None = None
    id: str = ""
    expect: Literal["answer", "fallback"] = "answer"

    @model_validator(mode="after")
    def _source_required_for_answers(self) -> TestCase:
        if self.expect == "answer" and not self.source:
            raise ValueError("'source' is required unless expect is 'fallback'")
        return self


def parse_testset(lines: Iterable[str]) -> list[TestCase]:
    """Parse JSONL text into cases, collecting every error before raising."""
    cases: list[TestCase] = []
    errors: list[str] = []
    seen: dict[str, int] = {}
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {number}: not valid JSON ({exc.msg})")
            continue
        if not isinstance(data, dict):
            errors.append(f"line {number}: expected a JSON object")
            continue
        try:
            case = TestCase.model_validate(data)
        except ValidationError as exc:
            for err in exc.errors():
                where = ".".join(str(part) for part in err["loc"]) or "row"
                errors.append(f"line {number}: {where}: {err['msg']}")
            continue
        if not case.id:
            case.id = f"line-{number}"
        key = " ".join(case.question.lower().split())
        if key in seen:
            errors.append(f"line {number}: duplicate question (first seen on line {seen[key]})")
            continue
        seen[key] = number
        cases.append(case)
    if errors:
        raise TestSetError("\n".join(errors))
    if not cases:
        raise TestSetError("the test set has no rows")
    ids = [c.id for c in cases]
    duplicate_ids = sorted({i for i in ids if ids.count(i) > 1})
    if duplicate_ids:
        raise TestSetError(f"duplicate id(s): {', '.join(duplicate_ids)}")
    return cases


def load_testset(path: Path) -> list[TestCase]:
    if not path.is_file():
        raise TestSetError(
            f"test set not found: {path}. Write it in the format of "
            "ai_service/eval/testset.example.jsonl (see the testset.py docstring)."
        )
    return parse_testset(path.read_text(encoding="utf-8-sig").splitlines())


def check_sources(cases: Iterable[TestCase], indexed: set[str]) -> None:
    """Every ``source`` must name a document that is in the knowledge base."""
    missing = sorted({c.source for c in cases if c.source and c.source not in indexed})
    if missing:
        known = ", ".join(sorted(indexed)) or "(none: the knowledge base is empty)"
        raise TestSetError(
            f"source(s) not in the knowledge base: {', '.join(missing)}. Indexed: {known}"
        )
