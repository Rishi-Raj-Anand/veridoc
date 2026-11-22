"""The evaluation runner without RAGAS or Ollama: rows, summary maths, outputs, setup checks."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from ai_service.config import EMERGENCY_NOTICE, get_settings
from ai_service.eval import run_ragas
from ai_service.eval.run_ragas import (
    RunRow,
    SetupError,
    build_pipelines,
    eligible_for_scoring,
    format_summary,
    load_runs,
    p95,
    run_case,
    run_pipelines,
    score_rows,
    summarize,
    write_outputs,
)
from ai_service.eval.testset import TestCase
from ai_service.ingestion.chunker import ParentChunk
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.retriever import Source
from ai_service.runtime import open_qdrant_sync

CASE = TestCase(id="cold", question="Cold symptoms?", reference="Runny nose.", source="disease.md")


class FakePipeline:
    """Stands in for the compiled graph: ``run_turn`` only calls ``invoke``."""

    def __init__(self, state: dict[str, Any] | None = None, fails: bool = False) -> None:
        self.state = state or {}
        self.fails = fails
        self.messages: list[str] = []

    def invoke(self, initial: dict[str, Any]) -> dict[str, Any]:
        self.messages.append(initial["message"])
        assert initial["history"] == []  # stateless: no transcript, no history
        if self.fails:
            raise RuntimeError("ollama went away")
        return {"intent": "medical", "iteration": 1, "attempts": [], **self.state}


def verified_state(answer: str = "Runny nose [1].") -> dict[str, Any]:
    return {
        "answer": answer,
        "citations": [{"n": 1, "filename": "disease.md", "page": 1}],
        "status": "verified",
        "emergency": answer.startswith(EMERGENCY_NOTICE),
        "iteration": 2,
        "attempts": [
            {"iteration": 1, "outcome": "fail", "best_score": 0.41},
            {"iteration": 2, "outcome": "pass", "best_score": 0.62},
        ],
        "sources": [
            Source(n=1, filename="disease.md", page=1, text="Cold: runny nose.", score=0.62),
            Source(n=2, filename="other.md", page=3, text="Unrelated.", score=0.4),
        ],
    }


def make_row(
    variant: str = "full",
    id: str = "q",
    *,
    status: str = "verified",
    intent: str = "medical",
    expect: str = "answer",
    iterations: int = 1,
    latency: float = 10.0,
    scores: dict[str, float | None] | None = None,
    cited: list[str] | None = None,
    error: str | None = None,
) -> RunRow:
    return RunRow(
        variant=variant,  # type: ignore[arg-type]
        id=id,
        question=f"{id}?",
        reference="ref",
        source="disease.md" if expect == "answer" else None,
        expect=expect,  # type: ignore[arg-type]
        intent=intent,
        status=status,
        iterations=iterations,
        latency_s=latency,
        cited_files=cited if cited is not None else ["disease.md"],
        scores=scores or {},
        error=error,
    )


# --- one question -----------------------------------------------------------------------


def test_a_row_keeps_what_ragas_and_the_report_need() -> None:
    pipeline = FakePipeline(verified_state())

    row = run_case(pipeline, CASE, "full")  # type: ignore[arg-type]

    assert pipeline.messages == ["Cold symptoms?"]
    assert (row.status, row.intent, row.iterations) == ("verified", "medical", 2)
    assert row.contexts == ["Cold: runny nose.", "Unrelated."]
    assert row.cited_files == ["disease.md"]
    assert row.retrieved_files == ["disease.md", "other.md"]
    assert row.best_score == 0.62
    assert row.source_hit is True
    assert eligible_for_scoring(row)


def test_the_emergency_notice_is_stripped_before_scoring() -> None:
    state = verified_state(f"{EMERGENCY_NOTICE}\n\nRunny nose [1].")

    row = run_case(FakePipeline(state), CASE, "full")  # type: ignore[arg-type]

    assert row.emergency
    assert row.answer == "Runny nose [1]."


def test_a_failing_question_is_recorded_and_does_not_stop_the_run() -> None:
    row = run_case(FakePipeline(fails=True), CASE, "single_pass")  # type: ignore[arg-type]

    assert row.error == "RuntimeError: ollama went away"
    assert row.variant == "single_pass"
    assert not eligible_for_scoring(row)


@pytest.mark.parametrize(
    ("changes", "eligible"),
    [
        ({}, True),
        ({"status": "fallback"}, False),
        ({"intent": "smalltalk", "status": "smalltalk"}, False),
        ({"expect": "fallback"}, False),
        ({"error": "boom"}, False),
    ],
)
def test_only_answers_to_in_corpus_medical_questions_are_scored(
    changes: dict[str, Any], eligible: bool
) -> None:
    assert eligible_for_scoring(make_row(**changes)) is eligible


# --- the run file -----------------------------------------------------------------------


def test_rows_are_appended_as_they_arrive_and_round_trip(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    pipelines = {"full": FakePipeline(verified_state()), "single_pass": FakePipeline(fails=True)}
    cases = [CASE, TestCase(id="b", question="Q2?", reference="r", source="disease.md")]

    rows = run_pipelines(pipelines, cases, runs)  # type: ignore[arg-type]

    assert [(r.variant, r.id) for r in rows] == [
        ("full", "cold"),
        ("full", "b"),
        ("single_pass", "cold"),
        ("single_pass", "b"),
    ]
    assert load_runs(runs) == rows
    assert all(json.loads(line)["variant"] for line in runs.read_text().splitlines())


def test_scoring_nothing_needs_no_ragas() -> None:
    score_rows([make_row(status="fallback")], client=None)  # type: ignore[arg-type]


# --- summary maths ----------------------------------------------------------------------


def test_p95_is_nearest_rank() -> None:
    assert p95([]) is None
    assert p95([3.0]) == 3.0
    assert p95([float(i) for i in range(1, 21)]) == 19.0
    assert p95([float(i) for i in range(1, 11)]) == 10.0


def test_the_summary_counts_rates_latency_and_routing() -> None:
    rows = [
        make_row(id="a", iterations=1, latency=10, scores={"faithfulness": 1.0}),
        make_row(id="b", iterations=3, latency=30, status="fallback"),
        make_row(
            id="c", iterations=2, latency=20, cited=["other.md"], scores={"faithfulness": 0.5}
        ),
        make_row(id="d", intent="out_of_scope", status="out_of_scope", latency=1, iterations=0),
        make_row(id="off1", expect="fallback", status="fallback", latency=5),
        make_row(id="off2", expect="fallback", status="verified", latency=5),
        make_row(id="e", error="boom", latency=99),
    ]

    (s,) = summarize(rows)

    assert (s.cases, s.errors, s.in_corpus, s.off_corpus) == (7, 1, 4, 2)
    assert s.routed_away == ["d (out_of_scope)"]
    assert s.answered_rate == 0.5  # a and c out of a, b, c, d
    assert s.fallback_rate == 0.25
    assert s.refusal_rate == 0.5
    assert s.avg_iterations == 1.6  # every medical row: a 1, b 3, c 2, off1 1, off2 1
    assert s.latency_median == 7.5  # errors excluded: 10, 30, 20, 1, 5, 5
    assert s.latency_p95 == 30
    assert s.source_hit_rate == 0.5  # a cites disease.md, c does not
    assert s.scored == 2
    assert s.metrics["faithfulness"] == 0.75
    assert s.metrics["answer_relevancy"] is None
    assert s.paired == 0  # one variant: no pairing


def test_paired_metrics_use_only_questions_both_variants_answered() -> None:
    rows = [
        make_row("full", "a", scores={"faithfulness": 1.0}),
        make_row("full", "b", scores={"faithfulness": 0.9}),
        make_row("single_pass", "a", scores={"faithfulness": 0.6}),
        make_row("single_pass", "c", scores={"faithfulness": 0.2}),
        make_row("single_pass", "b", status="fallback"),
    ]

    full, single = summarize(rows)

    assert (full.variant, single.variant) == ("full", "single_pass")
    assert (full.paired, single.paired) == (1, 1)
    assert full.paired_metrics["faithfulness"] == 1.0
    assert single.paired_metrics["faithfulness"] == 0.6
    assert full.metrics["faithfulness"] == 0.95
    assert single.metrics["faithfulness"] == 0.4


def test_outputs_are_written_and_the_summary_names_routed_questions(tmp_path: Path) -> None:
    rows = [
        make_row("full", "a", scores={"faithfulness": 1.0}),
        make_row("full", "d", intent="smalltalk", status="smalltalk"),
    ]

    summaries = write_outputs(tmp_path, "20261122-0900", rows)
    text = format_summary(summaries)

    with (tmp_path / "results-20261122-0900.csv").open(encoding="utf-8-sig") as f:
        results = list(csv.DictReader(f))
    with (tmp_path / "summary-20261122-0900.csv").open(encoding="utf-8-sig") as f:
        (summary,) = list(csv.DictReader(f))
    assert [r["id"] for r in results] == ["a", "d"]
    assert results[0]["faithfulness"] == "1.0"
    assert results[1]["faithfulness"] == ""
    assert summary["routed_away"] == "d (smalltalk)"
    assert "routed away from medical: d (smalltalk)" in text
    assert "compare runs relatively" in text


# --- setup checks -----------------------------------------------------------------------


def test_a_locked_qdrant_says_to_stop_the_ai_service() -> None:
    settings = get_settings()
    settings.vectors_dir.mkdir(parents=True)
    held = open_qdrant_sync(settings)  # what the running AI service holds
    try:
        with pytest.raises(SetupError, match=r"stop the AI service \(Ctrl\+C"):
            build_pipelines(settings, ["full"])
    finally:
        held.close()


def test_an_empty_knowledge_base_is_refused() -> None:
    settings = get_settings()
    settings.vectors_dir.mkdir(parents=True)

    with pytest.raises(SetupError, match="knowledge base is empty"):
        build_pipelines(settings, ["full", "single_pass"])

    open_qdrant_sync(settings).close()  # the client was released


class FakeModels:
    def __init__(self, models: set[str] | None = None) -> None:
        self.models = models

    def list_models(self) -> set[str]:
        if self.models is None:
            raise run_ragas.OllamaError("cannot reach Ollama")
        return self.models


def test_missing_models_and_a_down_ollama_are_setup_errors() -> None:
    settings = get_settings()

    run_ragas.check_models(FakeModels({"qwen3:8b", "bge-m3:latest"}), settings)  # type: ignore[arg-type]
    with pytest.raises(SetupError, match="not pulled: bge-m3"):
        run_ragas.check_models(FakeModels({"qwen3:8b"}), settings)  # type: ignore[arg-type]
    with pytest.raises(SetupError, match="Start Ollama"):
        run_ragas.check_models(FakeModels(None), settings)  # type: ignore[arg-type]


def write_testset(path: Path, *rows: dict[str, Any]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def index_document(filename: str) -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    Docstore(settings.docstore_path).replace_document(
        1, filename, [ParentChunk("1:p00000", 0, 1, 1, "text")]
    )


def test_check_validates_against_the_docstore_without_opening_qdrant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index_document("disease.md")
    good = write_testset(
        tmp_path / "good.jsonl",
        {"question": "Q1?", "reference": "r", "source": "disease.md"},
        {"question": "Q2?", "reference": "r", "source": None, "expect": "fallback"},
    )
    bad = write_testset(
        tmp_path / "bad.jsonl", {"question": "Q1?", "reference": "r", "source": "nope.pdf"}
    )
    held = open_qdrant_sync(get_settings())  # the AI service may be running during --check
    try:
        assert run_ragas.main(["--check", "--testset", str(good)]) == 0
        assert "OK: 2 rows (1 in-corpus, 1 off-corpus) from 1 document(s): disease.md" in (
            capsys.readouterr().out
        )
        assert run_ragas.main(["--check", "--testset", str(bad)]) == run_ragas.EXIT_SETUP
        assert run_ragas.main(["--check", "--testset", str(tmp_path / "x.jsonl")]) == 2
    finally:
        held.close()


def test_limit_keeps_the_first_rows(tmp_path: Path) -> None:
    index_document("disease.md")
    path = write_testset(
        tmp_path / "t.jsonl",
        *({"question": f"Q{i}?", "reference": "r", "source": "disease.md"} for i in range(5)),
    )

    cases = run_ragas.load_cases(path, get_settings(), 2)

    assert [c.question for c in cases] == ["Q0?", "Q1?"]
