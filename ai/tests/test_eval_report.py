"""Report assets (Phase 8.5): tables and SVGs from hand-made evaluation output."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from ai_service.eval import report

SUMMARY = [
    {
        "variant": "full",
        "in_corpus": "50",
        "off_corpus": "5",
        "answered_rate": "0.96",
        "fallback_rate": "0.0",
        "refusal_rate": "1.0",
        "faithfulness": "0.97",
        "paired_faithfulness": "0.96",
        "answer_relevancy": "0.88",
        "context_precision": "0.93",
        "avg_iterations": "1.3",
        "latency_mean": "17.2",
        "latency_median": "16.3",
        "latency_p95": "36.6",
        "source_hit_rate": "0.88",
    },
    {
        "variant": "single_pass",
        "in_corpus": "50",
        "off_corpus": "5",
        "answered_rate": "0.9",
        "fallback_rate": "0.06",
        "refusal_rate": "1.0",
        "faithfulness": "0.92",
        "paired_faithfulness": "",
        "answer_relevancy": "0.88",
        "context_precision": "0.94",
        "avg_iterations": "1.0",
        "latency_mean": "13.7",
        "latency_median": "13.9",
        "latency_p95": "16.1",
        "source_hit_rate": "0.89",
    },
]


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def results_rows() -> list[dict[str, str]]:
    rows = []
    for variant, latencies in (("full", [0.4, 15.0, 16.5, 37.0]), ("single_pass", [13.0, 14.0])):
        for i, latency in enumerate(latencies):
            rows.append({"variant": variant, "id": f"q{i}", "latency_s": str(latency), "error": ""})
    return rows


def runs_file(path: Path) -> Path:
    rows = [
        {
            "latency_s": 12.0,
            "iterations": 1,
            "attempts": [{"iteration": 1, "seconds": 11.0}],
            "llm_calls": [
                {"purpose": "generate", "prompt_tokens": 2400},
                {"purpose": "verify", "prompt_tokens": 2900},
                {"purpose": "embed", "prompt_tokens": 12},
            ],
        },
        {
            "latency_s": 40.0,
            "iterations": 3,
            "attempts": [
                {"iteration": 1, "seconds": 10.0},
                {"iteration": 2, "seconds": 14.0},
                {"iteration": 3, "seconds": 15.0},
            ],
            "llm_calls": [{"purpose": "verify", "prompt_tokens": 7100}],
        },
        {"latency_s": 1.0, "error": "OllamaError: down", "attempts": [], "llm_calls": []},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_the_ablation_table_matches_the_summary() -> None:
    table = report.ablation_table(SUMMARY)

    assert "| | Full dual-agent loop | Single pass (no Verifier) |" in table
    assert "| In-corpus questions answered | 96% | 90% |" in table
    assert "| Faithfulness | 0.97 | 0.92 |" in table
    assert "| Faithfulness, paired subset | 0.96 | - |" in table  # a blank cell stays visible
    assert "| Latency mean / median / p95 (s) | 17.2 / 16.3 / 36.6 | 13.7 / 13.9 / 16.1 |" in table


def test_performance_tables_split_iterations_and_flag_the_budget(tmp_path: Path) -> None:
    perf = report.perf_from_runs(report.read_runs(runs_file(tmp_path / "runs.jsonl")))

    assert perf.rows == 2  # the errored row is left out
    assert perf.iteration_seconds == {1: [11.0, 10.0], 2: [14.0], 3: [15.0]}
    assert "embed" not in perf.prompt_tokens  # embeddings are not LLM prompts
    tables = report.perf_tables(perf)
    assert "| Iteration 2 | 1 | 14.0 | 14.0 | 14.0 |" in tables
    assert "| Turns that used all 3 iterations | 1 | 40.0 | 40.0 | 40.0 |" in tables
    assert "| generate | 1 | 2,400 | 2,400 | 2,400 | yes |" in tables
    assert "| verify | 2 | 5,000 | 7,100 | 7,100 | **no** |" in tables


def test_the_swarm_keeps_every_x_and_separates_neighbours() -> None:
    xs = [100.0, 101.0, 102.0, 300.0]

    placed = report.swarm(xs)

    assert sorted(x for x, _ in placed) == xs
    lanes_near_100 = [lane for x, lane in placed if x < 200]
    assert len(set(lanes_near_100)) == 3  # three overlapping dots, three lanes
    assert dict(placed)[300.0] == 0  # a dot on its own stays in the centre


def test_main_writes_valid_svgs_and_the_markdown(tmp_path: Path) -> None:
    summary = write_csv(tmp_path / "summary.csv", SUMMARY)
    results = write_csv(tmp_path / "results.csv", results_rows())
    runs = runs_file(tmp_path / "runs.jsonl")
    out = tmp_path / "out"

    code = report.main(
        [
            "--summary", str(summary),
            "--results", str(results),
            "--perf-runs", str(runs),
            "--out", str(out),
        ]
    )  # fmt: skip

    assert code == 0
    metrics = (out / "metrics.md").read_text(encoding="utf-8")
    assert "## Ablation: full dual-agent loop vs single pass" in metrics
    assert "## Performance (Phase 8.4)" in metrics
    for name in ("ablation.svg", "latency.svg"):
        root = ET.fromstring((out / name).read_text(encoding="utf-8"))  # well-formed XML
        assert root.tag.endswith("svg")
    ablation = (out / "ablation.svg").read_text(encoding="utf-8")
    assert "96%" in ablation and "0.97" in ablation
    assert "prefers-color-scheme: dark" in ablation
    latency = (out / "latency.svg").read_text(encoding="utf-8")
    assert latency.count("<circle") == 6
    assert "routed away" in latency  # the 0.4 s refusal is explained
