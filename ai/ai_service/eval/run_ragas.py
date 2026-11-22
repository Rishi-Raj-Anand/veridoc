"""Offline RAGAS evaluation and the single-pass ablation (CLAUDE.md §13, tasks 7.2-7.3).

Run from ``ai/`` **with the AI service stopped** (Qdrant local mode allows one process)::

    uv sync --group eval
    uv run python -m ai_service.eval.run_ragas --check          # validate the test set only
    uv run python -m ai_service.eval.run_ragas                  # both variants + RAGAS
    uv run python -m ai_service.eval.run_ragas --limit 3        # quick smoke run
    uv run python -m ai_service.eval.run_ragas --score data/eval/runs-<ts>.jsonl

Two variants of the **production** pipeline (built by ``runtime.build_runtime``) answer every
question in-process, statelessly (``store=None``):

- ``full``: the dual-agent loop that ships (Verifier, escalation, fallback).
- ``single_pass``: iteration 1 only, no Verifier, the Phase 3 guards (the ablation baseline).

Raw outputs are appended to ``data/eval/runs-<ts>.jsonl`` as they arrive, so ``--score`` can
re-run RAGAS without re-running the pipeline. RAGAS scores only rows that produced an answer
to an in-corpus question; refusals are reported as rates. Outputs: ``results-<ts>.csv`` (one
row per question and variant), ``summary-<ts>.csv`` (one row per variant) and the summary on
stdout. A local 8B judge is weaker than GPT-4-class judges: compare runs relatively.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import statistics
import sys
import time
import uuid
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ai_service.config import EMERGENCY_NOTICE, Settings, get_settings
from ai_service.eval.testset import TestCase, TestSetError, check_sources, load_testset
from ai_service.graph.pipeline import PipelineResult, run_turn
from ai_service.llm.ollama_client import OllamaClient, OllamaError, normalise_model_name
from ai_service.retrieval.docstore import Docstore

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger("ai_service.eval")

Variant = Literal["full", "single_pass"]
VARIANTS: tuple[Variant, ...] = ("full", "single_pass")
METRICS = ("faithfulness", "answer_relevancy", "context_precision")
EVAL_USER_ID = 0
ITERATION_LEVELS = (1, 2, 3)  # §6.3
DEFAULT_TESTSET = Path(__file__).with_name("testset.jsonl")
EXIT_SETUP = 2
# Matches ragas_adapter.JUDGE_TIMEOUT_SECONDS (not imported: that module needs ragas).
EVAL_REQUEST_TIMEOUT_SECONDS = 600.0

LOCKED_MESSAGE = (
    "Qdrant storage at {path} is locked by another process. Qdrant local mode allows one "
    "process only - stop the AI service (Ctrl+C in its terminal) before running the evaluation."
)


class RunRow(BaseModel):
    """One question answered by one variant: everything needed to score it later."""

    variant: Variant
    id: str
    question: str
    reference: str
    source: str | None
    expect: Literal["answer", "fallback"]
    intent: str = ""
    status: str = ""
    emergency: bool = False
    iterations: int = 0
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    latency_s: float = 0.0
    best_score: float | None = None
    answer: str = ""
    contexts: list[str] = Field(default_factory=list)
    cited_files: list[str] = Field(default_factory=list)
    retrieved_files: list[str] = Field(default_factory=list)
    error: str | None = None
    scores: dict[str, float | None] = Field(default_factory=dict)
    # Every Ollama call of the turn (purpose, tokens, seconds); empty in Phase 7 runs files.
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def max_prompt_tokens(self) -> int | None:
        counts = [c["prompt_tokens"] for c in self.llm_calls if c.get("prompt_tokens") is not None]
        return max(counts, default=None)

    @property
    def key(self) -> tuple[str, str]:
        return (self.variant, self.id)

    @property
    def answered(self) -> bool:
        return self.error is None and self.status == "verified"

    @property
    def refused(self) -> bool:
        return self.error is None and self.status in ("fallback", "out_of_scope")

    @property
    def source_hit(self) -> bool | None:
        """Did an answered row cite the document the question was written from?"""
        if not self.answered or not self.source:
            return None
        return self.source in self.cited_files


def strip_notice(answer: str) -> str:
    """Drop the §7.2 emergency prefix: the judge should score the answer, not the notice."""
    if answer.startswith(EMERGENCY_NOTICE):
        return answer[len(EMERGENCY_NOTICE) :].lstrip()
    return answer


def to_row(case: TestCase, variant: Variant, result: PipelineResult, latency: float) -> RunRow:
    attempts = [dict(a) for a in result.attempts]
    return RunRow(
        variant=variant,
        id=case.id,
        question=case.question,
        reference=case.reference,
        source=case.source,
        expect=case.expect,
        intent=result.intent,
        status=result.status,
        emergency=result.emergency,
        iterations=result.iterations,
        attempts=attempts,
        latency_s=round(latency, 3),
        best_score=attempts[-1].get("best_score") if attempts else None,
        answer=strip_notice(result.answer),
        contexts=[s.text for s in result.sources],
        cited_files=sorted({c["filename"] for c in result.citations}),
        retrieved_files=sorted({s.filename for s in result.sources}),
        llm_calls=[dict(c) for c in result.llm_calls],
    )


def run_case(pipeline: CompiledStateGraph, case: TestCase, variant: Variant) -> RunRow:
    """Answer one question statelessly; an error is recorded on the row, not raised."""
    started = time.perf_counter()
    try:
        result = run_turn(
            pipeline,
            store=None,
            session_id=str(uuid.uuid4()),
            user_id=EVAL_USER_ID,
            message=case.question,
        )
    except Exception as exc:
        logger.exception("[%s] %s failed", variant, case.id)
        return RunRow(
            variant=variant,
            id=case.id,
            question=case.question,
            reference=case.reference,
            source=case.source,
            expect=case.expect,
            latency_s=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )
    return to_row(case, variant, result, time.perf_counter() - started)


def run_pipelines(
    pipelines: dict[Variant, CompiledStateGraph],
    cases: Sequence[TestCase],
    runs_path: Path,
) -> list[RunRow]:
    """Every case through every variant, variant by variant; each row is appended at once."""
    rows: list[RunRow] = []
    total = len(cases) * len(pipelines)
    with runs_path.open("a", encoding="utf-8") as out:
        for variant, pipeline in pipelines.items():
            for case in cases:
                row = run_case(pipeline, case, variant)
                rows.append(row)
                out.write(row.model_dump_json() + "\n")
                out.flush()
                logger.info(
                    "[%d/%d] %s %s: intent=%s status=%s iterations=%d %.1fs%s",
                    len(rows),
                    total,
                    variant,
                    case.id,
                    row.intent,
                    row.status,
                    row.iterations,
                    row.latency_s,
                    f" ERROR {row.error}" if row.error else "",
                )
    return rows


def load_runs(path: Path) -> list[RunRow]:
    rows = [
        RunRow.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"no rows in {path}")
    return rows


def eligible_for_scoring(row: RunRow) -> bool:
    """RAGAS judges answers to in-corpus medical questions; a refusal is not a bad answer."""
    return row.expect == "answer" and row.intent == "medical" and row.answered


# --- RAGAS scoring (needs `uv sync --group eval`) ------------------------------------------


def score_rows(rows: Sequence[RunRow], client: OllamaClient) -> None:
    """Fill ``row.scores`` for every eligible row, in place.

    Two passes keep model swaps down (§6.8): Faithfulness and Context Precision use only the
    LLM; Response Relevancy alternates the LLM and ``bge-m3`` on every row.
    """
    targets = [r for r in rows if eligible_for_scoring(r)]
    if not targets:
        logger.warning("no answered in-corpus rows to score")
        return
    try:
        from ragas import EvaluationDataset, evaluate

        from ai_service.eval.ragas_adapter import (
            OllamaRagasEmbeddings,
            OllamaRagasLLM,
            judge_run_config,
        )
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SystemExit(
            f"RAGAS is not installed ({exc}). Run `uv sync --group eval` in ai/ first, "
            "or pass --no-ragas for pipeline stats only."
        ) from exc

    llm = OllamaRagasLLM(client)
    embeddings = OllamaRagasEmbeddings(client)
    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": r.question,
                "response": r.answer,
                "retrieved_contexts": r.contexts,
                "reference": r.reference,
            }
            for r in targets
        ]
    )
    for names in (("faithfulness", "context_precision"), ("answer_relevancy",)):
        metrics = build_metrics(names, llm, embeddings)
        logger.info("RAGAS: scoring %d rows on %s", len(targets), ", ".join(names))
        # The metrics already carry the judge and embeddings (build_metrics); passing the
        # embeddings again would only make evaluate() re-wrap them in a LangChain wrapper.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # evaluate() -> @experiment
            result = evaluate(
                dataset,
                metrics=metrics,
                llm=llm,
                run_config=judge_run_config(),
                raise_exceptions=False,
                show_progress=True,
            )
        for row, scores in zip(targets, result.scores, strict=True):
            new = {
                name: clean_score(scores.get(m.name))
                for name, m in zip(names, metrics, strict=True)
            }
            row.scores = {**row.scores, **new}  # a fresh dict: never mutate a shared one


def build_metrics(names: Iterable[str], llm: Any, embeddings: Any) -> list[Any]:
    """The legacy RAGAS metrics, with our judge attached to every one of them.

    ``evaluate()`` silently falls back to OpenAI for any metric without an LLM, so the judge is
    set here and checked, never left to RAGAS' defaults (§1: local AI only).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithReference,
            ResponseRelevancy,
        )

    factories: dict[str, Callable[[], Any]] = {
        "faithfulness": lambda: Faithfulness(llm=llm),
        "answer_relevancy": lambda: ResponseRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": lambda: LLMContextPrecisionWithReference(llm=llm),
    }
    metrics = [factories[name]() for name in names]
    for metric in metrics:
        if getattr(metric, "llm", None) is not llm:
            raise RuntimeError(f"RAGAS metric {metric.name} has no local judge attached")
        if hasattr(metric, "embeddings") and metric.embeddings is not embeddings:
            raise RuntimeError(f"RAGAS metric {metric.name} has no local embeddings attached")
    return metrics


def clean_score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, 4)


# --- Summary --------------------------------------------------------------------------------


@dataclass
class VariantSummary:
    variant: str
    cases: int
    errors: int
    in_corpus: int
    off_corpus: int
    routed_away: list[str] = field(default_factory=list)
    answered_rate: float | None = None
    fallback_rate: float | None = None
    refusal_rate: float | None = None
    avg_iterations: float | None = None
    latency_mean: float | None = None
    latency_median: float | None = None
    latency_p95: float | None = None
    source_hit_rate: float | None = None
    # Phase 8.4: mean seconds of iteration 1/2/3, and the largest prompt any call sent.
    iteration_seconds: dict[int, float | None] = field(default_factory=dict)
    max_prompt_tokens: int | None = None
    scored: int = 0
    metrics: dict[str, float | None] = field(default_factory=dict)
    paired: int = 0
    paired_metrics: dict[str, float | None] = field(default_factory=dict)


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def _mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.fmean(present), 4) if present else None


def p95(values: Sequence[float]) -> float | None:
    """Nearest-rank 95th percentile (well defined for the small samples used here)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def iteration_seconds(rows: Sequence[RunRow]) -> dict[int, float | None]:
    """Mean duration of each iteration level (retrieve to guard/audit), over all attempts."""
    return {
        level: _mean(
            a.get("seconds") for r in rows for a in r.attempts if a.get("iteration") == level
        )
        for level in ITERATION_LEVELS
    }


def summarize(rows: Sequence[RunRow]) -> list[VariantSummary]:
    variants = [v for v in VARIANTS if any(r.variant == v for r in rows)]
    answered_ids = {
        v: {r.id for r in rows if r.variant == v and eligible_for_scoring(r) and r.scores}
        for v in variants
    }
    paired_ids = set.intersection(*answered_ids.values()) if len(variants) > 1 else set()
    summaries = []
    for variant in variants:
        mine = [r for r in rows if r.variant == variant]
        ok = [r for r in mine if r.error is None]
        in_corpus = [r for r in ok if r.expect == "answer"]
        off_corpus = [r for r in ok if r.expect == "fallback"]
        medical = [r for r in ok if r.intent == "medical"]  # off-corpus escalations cost too
        answered = [r for r in in_corpus if r.answered]
        hits = [r.source_hit for r in answered if r.source_hit is not None]
        latencies = [r.latency_s for r in ok]
        scored = [r for r in mine if eligible_for_scoring(r) and r.scores]
        paired = [r for r in scored if r.id in paired_ids]
        summaries.append(
            VariantSummary(
                variant=variant,
                cases=len(mine),
                errors=len(mine) - len(ok),
                in_corpus=len(in_corpus),
                off_corpus=len(off_corpus),
                routed_away=[f"{r.id} ({r.intent})" for r in in_corpus if r.intent != "medical"],
                answered_rate=_rate(len(answered), len(in_corpus)),
                fallback_rate=_rate(sum(r.status == "fallback" for r in in_corpus), len(in_corpus)),
                refusal_rate=_rate(sum(r.refused for r in off_corpus), len(off_corpus)),
                avg_iterations=_mean(float(r.iterations) for r in medical),
                latency_mean=_mean(latencies),
                latency_median=round(statistics.median(latencies), 3) if latencies else None,
                latency_p95=p95(latencies),
                source_hit_rate=_rate(sum(hits), len(hits)),
                iteration_seconds=iteration_seconds(ok),
                max_prompt_tokens=max(
                    (r.max_prompt_tokens for r in ok if r.max_prompt_tokens is not None),
                    default=None,
                ),
                scored=len(scored),
                metrics={m: _mean(r.scores.get(m) for r in scored) for m in METRICS},
                paired=len(paired),
                paired_metrics={m: _mean(r.scores.get(m) for r in paired) for m in METRICS},
            )
        )
    return summaries


SUMMARY_FIELDS = (
    "variant",
    "cases",
    "errors",
    "in_corpus",
    "off_corpus",
    "routed_away",
    "answered_rate",
    "fallback_rate",
    "refusal_rate",
    "avg_iterations",
    "latency_mean",
    "latency_median",
    "latency_p95",
    "source_hit_rate",
    *(f"iteration{level}_seconds" for level in ITERATION_LEVELS),
    "max_prompt_tokens",
    "scored",
    *METRICS,
    "paired",
    *(f"paired_{m}" for m in METRICS),
)

RESULT_FIELDS = (
    "variant",
    "id",
    "expect",
    "source",
    "intent",
    "status",
    "emergency",
    "iterations",
    "best_score",
    "latency_s",
    "attempt_seconds",
    "max_prompt_tokens",
    "source_hit",
    "retrieved_source",
    *METRICS,
    "outcomes",
    "cited_files",
    "question",
    "answer",
    "error",
)


def summary_record(s: VariantSummary) -> dict[str, Any]:
    record: dict[str, Any] = {
        "variant": s.variant,
        "cases": s.cases,
        "errors": s.errors,
        "in_corpus": s.in_corpus,
        "off_corpus": s.off_corpus,
        "routed_away": "; ".join(s.routed_away),
        "answered_rate": s.answered_rate,
        "fallback_rate": s.fallback_rate,
        "refusal_rate": s.refusal_rate,
        "avg_iterations": s.avg_iterations,
        "latency_mean": s.latency_mean,
        "latency_median": s.latency_median,
        "latency_p95": s.latency_p95,
        "source_hit_rate": s.source_hit_rate,
        **{f"iteration{k}_seconds": v for k, v in s.iteration_seconds.items()},
        "max_prompt_tokens": s.max_prompt_tokens,
        "scored": s.scored,
        "paired": s.paired,
    }
    for m in METRICS:
        record[m] = s.metrics.get(m)
        record[f"paired_{m}"] = s.paired_metrics.get(m)
    return record


def result_record(row: RunRow) -> dict[str, Any]:
    return {
        "variant": row.variant,
        "id": row.id,
        "expect": row.expect,
        "source": row.source or "",
        "intent": row.intent,
        "status": row.status,
        "emergency": row.emergency,
        "iterations": row.iterations,
        "best_score": row.best_score,
        "latency_s": row.latency_s,
        "attempt_seconds": " ".join(
            f"{a['seconds']:.1f}" for a in row.attempts if a.get("seconds") is not None
        ),
        "max_prompt_tokens": row.max_prompt_tokens,
        "source_hit": row.source_hit,
        "retrieved_source": (row.source in row.retrieved_files) if row.source else None,
        **{m: row.scores.get(m) for m in METRICS},
        "outcomes": " ".join(str(a.get("outcome")) for a in row.attempts),
        "cited_files": "; ".join(row.cited_files),
        "question": row.question,
        "answer": row.answer,
        "error": row.error or "",
    }


def write_csv(path: Path, fields: Sequence[str], records: Iterable[dict[str, Any]]) -> None:
    # utf-8-sig so Excel opens the dashes and the emergency sign correctly.
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(fields))
        writer.writeheader()
        for record in records:
            writer.writerow({k: "" if v is None else v for k, v in record.items()})


def _fmt(value: float | None, percent: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.0f}%" if percent else f"{value:.2f}"


def format_summary(summaries: Sequence[VariantSummary]) -> str:
    lines = ["", "VeriDoc AI evaluation summary", "=" * 29]
    table = [
        ("cases (in-corpus / off-corpus)", lambda s: f"{s.cases} ({s.in_corpus}/{s.off_corpus})"),
        ("errors", lambda s: str(s.errors)),
        ("routed away from medical", lambda s: str(len(s.routed_away))),
        ("answered (verified) rate", lambda s: _fmt(s.answered_rate, True)),
        ("fallback rate", lambda s: _fmt(s.fallback_rate, True)),
        ("off-corpus refused", lambda s: _fmt(s.refusal_rate, True)),
        ("avg iterations", lambda s: _fmt(s.avg_iterations)),
        ("latency mean / median / p95 (s)", _latency_cell),
        ("iteration 1 / 2 / 3 mean (s)", _iteration_cell),
        ("max prompt tokens", lambda s: str(s.max_prompt_tokens or "-")),
        ("cites the expected source", lambda s: _fmt(s.source_hit_rate, True)),
        ("scored answers", lambda s: str(s.scored)),
        *((f"{m}", _metric_cell(m, paired=False)) for m in METRICS),
        ("paired answers", lambda s: str(s.paired)),
        *((f"{m} (paired)", _metric_cell(m, paired=True)) for m in METRICS),
    ]
    width = max(len(label) for label, _ in table)
    lines.append(f"{'':<{width}}  " + "  ".join(f"{s.variant:>16}" for s in summaries))
    for label, cell in table:
        lines.append(f"{label:<{width}}  " + "  ".join(f"{cell(s):>16}" for s in summaries))
    for s in summaries:
        if s.routed_away:
            lines.append(f"{s.variant}: routed away from medical: {', '.join(s.routed_away)}")
    lines.append("A local 8B judge is weaker than GPT-4-class judges: compare runs relatively.")
    return "\n".join(lines)


def _latency_cell(s: VariantSummary) -> str:
    return f"{_fmt(s.latency_mean)}/{_fmt(s.latency_median)}/{_fmt(s.latency_p95)}"


def _iteration_cell(s: VariantSummary) -> str:
    return "/".join(_fmt(s.iteration_seconds.get(level)) for level in ITERATION_LEVELS)


def _metric_cell(metric: str, *, paired: bool) -> Callable[[VariantSummary], str]:
    def cell(s: VariantSummary) -> str:
        return _fmt((s.paired_metrics if paired else s.metrics).get(metric))

    return cell


def write_outputs(eval_dir: Path, stamp: str, rows: Sequence[RunRow]) -> list[VariantSummary]:
    summaries = summarize(rows)
    results_path = eval_dir / f"results-{stamp}.csv"
    summary_path = eval_dir / f"summary-{stamp}.csv"
    write_csv(results_path, RESULT_FIELDS, (result_record(r) for r in rows))
    write_csv(summary_path, SUMMARY_FIELDS, (summary_record(s) for s in summaries))
    logger.info("wrote %s", results_path)
    logger.info("wrote %s", summary_path)
    return summaries


# --- Setup checks and CLI -------------------------------------------------------------------


class SetupError(RuntimeError):
    """A precondition failed; the message says how to fix it."""


def make_stamp(eval_dir: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if (eval_dir / f"results-{stamp}.csv").exists() or (eval_dir / f"runs-{stamp}.jsonl").exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return stamp


def indexed_filenames(settings: Settings) -> set[str]:
    """Filenames in the docstore. SQLite only, so it works while the AI service is running."""
    if not settings.docstore_path.is_file():
        return set()
    return Docstore(settings.docstore_path).filenames()


def load_cases(path: Path, settings: Settings, limit: int | None) -> list[TestCase]:
    try:
        cases = load_testset(path)
        check_sources(cases, indexed_filenames(settings))
    except TestSetError as exc:
        raise SetupError(f"{path}:\n{exc}") from exc
    return cases[:limit] if limit else cases


def check_models(client: OllamaClient, settings: Settings) -> None:
    try:
        available = client.list_models()
    except OllamaError as exc:
        raise SetupError(f"{exc}. Start Ollama and try again.") from exc
    missing = [
        name
        for name in (settings.llm_model, settings.embed_model)
        if normalise_model_name(name) not in available
    ]
    if missing:
        raise SetupError(f"Ollama model(s) not pulled: {', '.join(missing)} (`ollama pull ...`)")


def build_pipelines(
    settings: Settings, variants: Sequence[Variant]
) -> tuple[dict[Variant, CompiledStateGraph], Callable[[], None]]:
    """Open Qdrant (failing fast if the AI service holds it) and build each variant."""
    from ai_service.runtime import QdrantOpenError, build_runtime, open_qdrant_sync

    try:
        client = open_qdrant_sync(settings)
    except QdrantOpenError as exc:
        raise SetupError(LOCKED_MESSAGE.format(path=settings.vectors_dir)) from exc
    pipelines: dict[Variant, CompiledStateGraph] = {}
    for variant in variants:
        if variant == "full":
            runtime = build_runtime(settings, client)
        else:
            single = settings.model_copy(update={"max_iterations": 1})
            runtime = build_runtime(single, client, verify_drafts=False)
        if not pipelines and runtime.vector_store.count() == 0:
            client.close()
            raise SetupError("the knowledge base is empty: upload documents on the admin page")
        pipelines[variant] = runtime.pipeline
    return pipelines, client.close


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ai_service.eval.run_ragas",
        description="RAGAS evaluation of the VeriDoc AI pipeline (stop the AI service first).",
    )
    parser.add_argument("--testset", type=Path, default=DEFAULT_TESTSET)
    parser.add_argument("--variant", choices=[*VARIANTS, "both"], default="both")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--check", action="store_true", help="validate the test set and exit")
    parser.add_argument("--score", type=Path, default=None, help="re-score a runs-*.jsonl file")
    parser.add_argument("--no-ragas", action="store_true", help="pipeline stats only")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = parse_args(argv)
    settings = get_settings()
    settings.eval_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _run(args, settings)
    except SetupError as exc:
        logger.error("%s", exc)
        return EXIT_SETUP


def _run(args: argparse.Namespace, settings: Settings) -> int:
    # Judge prompts are long and the run is offline, so allow what RAGAS allows per call.
    client = OllamaClient(settings, request_timeout=EVAL_REQUEST_TIMEOUT_SECONDS)
    if args.score is not None:
        rows = load_runs(args.score)
        check_models(client, settings)
        score_rows(rows, client)
        stamp = make_stamp(settings.eval_dir)
        sys.stdout.write(format_summary(write_outputs(settings.eval_dir, stamp, rows)) + "\n")
        return 0

    cases = load_cases(args.testset, settings, args.limit)
    if args.check:
        off = sum(c.expect == "fallback" for c in cases)
        sources = sorted({c.source for c in cases if c.source})
        sys.stdout.write(
            f"OK: {len(cases)} rows ({len(cases) - off} in-corpus, {off} off-corpus) "
            f"from {len(sources)} document(s): {', '.join(sources)}\n"
        )
        return 0

    check_models(client, settings)
    variants: list[Variant] = list(VARIANTS) if args.variant == "both" else [args.variant]
    pipelines, close = build_pipelines(settings, variants)
    stamp = make_stamp(settings.eval_dir)
    runs_path = settings.eval_dir / f"runs-{stamp}.jsonl"
    logger.info("%d questions x %s -> %s", len(cases), ", ".join(variants), runs_path)
    try:
        rows = run_pipelines(pipelines, cases, runs_path)
    finally:
        close()
    if not args.no_ragas:
        score_rows(rows, client)
        # Rewrite the runs file with the scores, so --score is only needed after a crash.
        runs_path.write_text("".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
    sys.stdout.write(format_summary(write_outputs(settings.eval_dir, stamp, rows)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
