"""Report assets from evaluation output (CLAUDE.md §17, Phase 8.5). Standard library only.

Reads what ``run_ragas`` wrote to ``data/eval/`` and writes, into ``--out``:

- ``metrics.md``: the ablation table, pipeline stats and (with ``--perf-runs``) the
  per-iteration latency and prompt-size table from the Phase 8.4 performance run;
- ``ablation.svg``: answered rate, Faithfulness, Answer Relevancy and Context Precision,
  full loop against single pass, as grouped bars on one 0-1 axis;
- ``latency.svg``: every question's latency per variant as a dot strip, with mean and p95.

The SVGs are static (they go into a printed report), so each bar carries its value and the
Markdown tables are the table view. Colours are the dataviz reference palette's first two
categorical slots, with their dark-mode steps for viewers that prefer dark.

Usage, from ``ai/``::

    uv run python -m ai_service.eval.report --summary ..\\data\\eval\\summary-<ts>.csv
        --results ..\\data\\eval\\results-<ts>.csv --perf-runs ..\\data\\eval\\runs-<ts>.jsonl
        --out ..\\docs\\report
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

VARIANT_LABELS = {"full": "Full dual-agent loop", "single_pass": "Single pass (no Verifier)"}
VARIANT_ORDER = ("full", "single_pass")
ITERATION_LEVELS = (1, 2, 3)
PROMPT_BUDGET_TOKENS = 7000

# (summary column, chart label, table label)
ABLATION_METRICS = (
    ("answered_rate", "Answered (in-corpus)", "In-corpus questions answered"),
    ("faithfulness", "Faithfulness", "Faithfulness"),
    ("answer_relevancy", "Answer Relevancy", "Answer Relevancy"),
    ("context_precision", "Context Precision", "Context Precision"),
)

SVG_STYLE = """
  <style>
    .surface { fill: #fcfcfb; }
    .ink { fill: #0b0b0b; }
    .ink-2 { fill: #52514e; }
    .grid { stroke: #e4e3df; stroke-width: 1; }
    .axis { stroke: #a3a29c; stroke-width: 1; }
    .s1 { fill: #2a78d6; }
    .s2 { fill: #eb6834; }
    .gap { stroke: #fcfcfb; stroke-width: 2; }
    .tick { stroke: #0b0b0b; stroke-width: 2; }
    text { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    @media (prefers-color-scheme: dark) {
      .surface { fill: #1a1a19; }
      .ink { fill: #f0efec; }
      .ink-2 { fill: #c3c2b7; }
      .grid { stroke: #34332f; }
      .axis { stroke: #6b6a64; }
      .s1 { fill: #3987e5; }
      .s2 { fill: #d95926; }
      .gap { stroke: #1a1a19; }
      .tick { stroke: #f0efec; }
    }
  </style>"""


# --- reading ------------------------------------------------------------------------------


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def read_runs(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def number(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def by_variant(summary: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["variant"]: row for row in summary}


def p95(values: Sequence[float]) -> float | None:
    """Nearest-rank 95th percentile, the same rule as ``run_ragas.p95``."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


# --- tables -------------------------------------------------------------------------------


def fmt(value: float | None, *, percent: bool = False, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.0f}%" if percent else f"{value:.{digits}f}"


def ablation_table(summary: list[dict[str, str]]) -> str:
    rows = by_variant(summary)
    variants = [v for v in VARIANT_ORDER if v in rows]
    lines = [
        "| | " + " | ".join(VARIANT_LABELS[v] for v in variants) + " |",
        "|---|" + "---|" * len(variants),
    ]

    def add(label: str, cell: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(cell(rows[v]) for v in variants) + " |")

    def col(name: str, **kwargs: Any) -> Any:
        return lambda r: fmt(number(r.get(name)), **kwargs)

    add("Questions (in-corpus / off-corpus)", lambda r: f"{r['in_corpus']} / {r['off_corpus']}")
    add("In-corpus questions answered", col("answered_rate", percent=True))
    add("Fell back on an in-corpus question", col("fallback_rate", percent=True))
    add("Off-corpus questions refused", col("refusal_rate", percent=True))
    add("Faithfulness", col("faithfulness"))
    add("Faithfulness, paired subset", col("paired_faithfulness"))
    add("Answer Relevancy", col("answer_relevancy"))
    add("Context Precision", col("context_precision"))
    add("Average iterations", col("avg_iterations"))
    add(
        "Latency mean / median / p95 (s)",
        lambda r: " / ".join(
            fmt(number(r.get(k)), digits=1)
            for k in ("latency_mean", "latency_median", "latency_p95")
        ),
    )
    add("Cites the expected source", col("source_hit_rate", percent=True))
    return "\n".join(lines)


@dataclass
class Perf:
    rows: int
    turn_latencies: list[float]
    iteration_seconds: dict[int, list[float]]
    prompt_tokens: dict[str, list[int]]
    three_iteration_latencies: list[float]


def perf_from_runs(runs: list[dict[str, Any]]) -> Perf:
    ok = [r for r in runs if not r.get("error")]
    iteration_seconds: dict[int, list[float]] = {level: [] for level in ITERATION_LEVELS}
    prompt_tokens: dict[str, list[int]] = {}
    for row in ok:
        for attempt in row.get("attempts", []):
            seconds = attempt.get("seconds")
            if seconds is not None and attempt.get("iteration") in iteration_seconds:
                iteration_seconds[attempt["iteration"]].append(float(seconds))
        for call in row.get("llm_calls", []):
            if call.get("prompt_tokens") is not None and call.get("purpose") != "embed":
                prompt_tokens.setdefault(call["purpose"], []).append(int(call["prompt_tokens"]))
    return Perf(
        rows=len(ok),
        turn_latencies=[float(r["latency_s"]) for r in ok],
        iteration_seconds=iteration_seconds,
        prompt_tokens=prompt_tokens,
        three_iteration_latencies=[float(r["latency_s"]) for r in ok if r.get("iterations") == 3],
    )


def perf_tables(perf: Perf) -> str:
    def stats(values: Sequence[float]) -> str:
        if not values:
            return "- | - | -"
        return (
            f"{statistics.fmean(values):.1f} | {statistics.median(values):.1f} | {p95(values):.1f}"
        )

    lines = [
        f"Measured on {perf.rows} questions (full loop).",
        "",
        "| Latency (s) | n | Mean | Median | p95 |",
        "|---|---|---|---|---|",
        f"| Whole turn | {len(perf.turn_latencies)} | {stats(perf.turn_latencies)} |",
    ]
    for level in ITERATION_LEVELS:
        values = perf.iteration_seconds[level]
        lines.append(f"| Iteration {level} | {len(values)} | {stats(values)} |")
    if perf.three_iteration_latencies:
        values = perf.three_iteration_latencies
        lines.append(f"| Turns that used all 3 iterations | {len(values)} | {stats(values)} |")
    lines += [
        "",
        "| Prompt tokens (Ollama's count) | Calls | Mean | p95 | Max | "
        f"Under {PROMPT_BUDGET_TOKENS:,}? |",
        "|---|---|---|---|---|---|",
    ]
    for purpose in sorted(perf.prompt_tokens):
        values = perf.prompt_tokens[purpose]
        top = max(values)
        lines.append(
            f"| {purpose} | {len(values)} | {statistics.fmean(values):,.0f} | "
            f"{p95(values):,} | {top:,} | {'yes' if top < PROMPT_BUDGET_TOKENS else '**no**'} |"
        )
    return "\n".join(lines)


def metrics_markdown(
    summary: list[dict[str, str]], perf: Perf | None, sources: dict[str, Path]
) -> str:
    parts = [
        "# VeriDoc AI — evaluation metrics",
        "",
        "Generated by `ai_service/eval/report.py` from:",
        *(f"- {label}: `{path.name}`" for label, path in sources.items()),
        "",
        "## Ablation: full dual-agent loop vs single pass",
        "",
        "Scores are RAGAS 0.4.4 with the local `qwen3:8b` as judge, over answered in-corpus "
        "questions. Compare the columns relatively: an 8B judge is weaker than GPT-4-class "
        "judges, and a few points on ~50 questions is not significant.",
        "",
        ablation_table(summary),
        "",
        "![Ablation chart](ablation.svg)",
        "",
        "![Latency per question](latency.svg)",
    ]
    if perf is not None:
        parts += ["", "## Performance (Phase 8.4)", "", perf_tables(perf)]
    return "\n".join(parts) + "\n"


# --- charts -------------------------------------------------------------------------------


def svg_document(width: int, height: int, title: str, body: list[str]) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-labelledby="title">',
            f'  <title id="title">{escape(title)}</title>',
            SVG_STYLE,
            f'  <rect class="surface" x="0" y="0" width="{width}" height="{height}" rx="8"/>',
            *("  " + line for line in body),
            "</svg>",
            "",
        ]
    )


def legend(x: float, y: float, variants: Sequence[str]) -> list[str]:
    items = []
    for i, variant in enumerate(variants):
        cls = "s1" if variant == "full" else "s2"
        left = x + i * 230
        items += [
            f'<rect class="{cls}" x="{left}" y="{y - 10}" width="12" height="12" rx="3"/>',
            f'<text class="ink-2" x="{left + 18}" y="{y}" font-size="13">'
            f"{escape(VARIANT_LABELS[variant])}</text>",
        ]
    return items


def rounded_bar(x: float, y: float, width: float, height: float, cls: str) -> str:
    """A bar with 4px rounded data end (top) and a square baseline end (§ mark specs)."""
    r = min(4.0, width / 2, height)
    if height <= 0:
        return ""
    return (
        f'<path class="{cls} gap" d="M{x:.1f},{y + height:.1f} V{y + r:.1f} '
        f"Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + width - r:.1f} "
        f'Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + r:.1f} V{y + height:.1f} Z"/>'
    )


def ablation_svg(summary: list[dict[str, str]]) -> str:
    rows = by_variant(summary)
    variants = [v for v in VARIANT_ORDER if v in rows]
    width, height = 720, 400
    left, right, top, bottom = 56, 24, 70, 340
    plot_w, plot_h = width - left - right, bottom - top
    group_w = plot_w / len(ABLATION_METRICS)
    bar_w = 44.0
    body = [
        '<text class="ink" x="24" y="32" font-size="17" font-weight="600">'
        "Full loop vs single pass</text>",
        *legend(24, 56, variants),
    ]
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = bottom - tick * plot_h
        body += [
            f'<line class="{"axis" if tick == 0 else "grid"}" x1="{left}" y1="{y:.1f}" '
            f'x2="{width - right}" y2="{y:.1f}"/>',
            f'<text class="ink-2" x="{left - 8}" y="{y + 4:.1f}" font-size="11" '
            f'text-anchor="end">{tick:.2f}</text>',
        ]
    for i, (column, label, _) in enumerate(ABLATION_METRICS):
        center = left + group_w * (i + 0.5)
        start = center - (bar_w * len(variants) + 2 * (len(variants) - 1)) / 2
        for j, variant in enumerate(variants):
            value = number(rows[variant].get(column))
            if value is None:
                continue
            x = start + j * (bar_w + 2)
            h = value * plot_h
            cls = "s1" if variant == "full" else "s2"
            shown = f"{value * 100:.0f}%" if column.endswith("_rate") else f"{value:.2f}"
            body += [
                rounded_bar(x, bottom - h, bar_w, h, cls),
                f'<text class="ink" x="{x + bar_w / 2:.1f}" y="{bottom - h - 6:.1f}" '
                f'font-size="12" text-anchor="middle">{shown}</text>',
            ]
        body.append(
            f'<text class="ink-2" x="{center:.1f}" y="{bottom + 20}" font-size="12" '
            f'text-anchor="middle">{escape(label)}</text>'
        )
    body.append(
        f'<text class="ink-2" x="24" y="{height - 14}" font-size="11">RAGAS 0.4.4, local '
        "qwen3:8b judge. Scores over answered in-corpus questions; compare relatively.</text>"
    )
    return svg_document(width, height, "Full dual-agent loop vs single pass", body)


SWARM_LANES = (0, -10, 10, -20, 20, -30, 30)
QUICK_REPLY_SECONDS = 2.0


def swarm(xs: Sequence[float], spacing: float = 10.0) -> list[tuple[float, float]]:
    """Place dots in the first lane (centre outwards) where they overlap no earlier dot.

    A deterministic beeswarm: every dot stays at its true x, only its lane changes. When
    every lane is taken the dot goes in the centre lane, so no value is dropped.
    """
    placed: list[tuple[float, float]] = []
    for x in sorted(xs):
        lane = next(
            (
                offset
                for offset in SWARM_LANES
                if all(abs(x - px) >= spacing or po != offset for px, po in placed)
            ),
            0,
        )
        placed.append((x, lane))
    return placed


def latency_svg(results: list[dict[str, str]]) -> str:
    variants = [v for v in VARIANT_ORDER if any(r["variant"] == v for r in results)]
    values = {
        v: [
            x
            for r in results
            if r["variant"] == v and not r.get("error") and (x := number(r["latency_s"]))
        ]
        for v in variants
    }
    top_value = max((max(vs) for vs in values.values() if vs), default=1.0)
    scale_max = max(10.0, math.ceil(top_value / 10) * 10)
    width = 720
    left, right, top = 200, 32, 76
    row_h = 96
    height = top + row_h * len(variants) + 76
    plot_w = width - left - right

    def x_of(seconds: float) -> float:
        return left + seconds / scale_max * plot_w

    body = [
        '<text class="ink" x="24" y="32" font-size="17" font-weight="600">'
        "Latency per question (s)</text>",
        '<text class="ink-2" x="24" y="54" font-size="12">Each dot is one question. '
        "The tall tick is the mean, the short tick the p95.</text>",
    ]
    axis_y = top + row_h * len(variants)
    step = 10 if scale_max <= 60 else 20
    for tick in range(0, int(scale_max) + 1, step):
        x = x_of(tick)
        body += [
            f'<line class="{"axis" if tick == 0 else "grid"}" x1="{x:.1f}" y1="{top - 8}" '
            f'x2="{x:.1f}" y2="{axis_y}"/>',
            f'<text class="ink-2" x="{x:.1f}" y="{axis_y + 18}" font-size="11" '
            f'text-anchor="middle">{tick}</text>',
        ]
    for i, variant in enumerate(variants):
        center = top + row_h * i + row_h / 2
        cls = "s1" if variant == "full" else "s2"
        body.append(
            f'<text class="ink" x="{left - 12}" y="{center + 4:.1f}" font-size="13" '
            f'text-anchor="end">{escape(VARIANT_LABELS[variant])}</text>'
        )
        for cx, offset in swarm([x_of(s) for s in values[variant]]):
            body.append(
                f'<circle class="{cls} gap" cx="{cx:.1f}" cy="{center + offset:.1f}" r="4.5"/>'
            )
        if values[variant]:
            mean = statistics.fmean(values[variant])
            high = p95(values[variant]) or 0.0
            body += [
                f'<line class="tick" x1="{x_of(mean):.1f}" y1="{center - 38:.1f}" '
                f'x2="{x_of(mean):.1f}" y2="{center + 38:.1f}"/>',
                f'<text class="ink" x="{x_of(mean):.1f}" y="{center - 42:.1f}" font-size="11" '
                f'text-anchor="middle">mean {mean:.1f}</text>',
                f'<line class="tick" x1="{x_of(high):.1f}" y1="{center - 26:.1f}" '
                f'x2="{x_of(high):.1f}" y2="{center + 26:.1f}"/>',
                f'<text class="ink-2" x="{x_of(high):.1f}" y="{center + 48:.1f}" '
                f'font-size="11" text-anchor="middle">p95 {high:.1f}</text>',
            ]
    body.append(
        f'<text class="ink-2" x="{left + plot_w / 2:.1f}" y="{axis_y + 38}" font-size="12" '
        'text-anchor="middle">seconds per answer, including model swaps</text>'
    )
    quick = sum(1 for vs in values.values() for v in vs if v < QUICK_REPLY_SECONDS)
    if quick:
        body.append(
            f'<text class="ink-2" x="24" y="{axis_y + 58}" font-size="11">Dots under '
            f"{QUICK_REPLY_SECONDS:.0f} s are questions the intent classifier routed away from "
            "the medical pipeline (an instant refusal).</text>"
        )
    return svg_document(width, height, "Latency per question by variant", body)


# --- CLI ----------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--summary", type=Path, required=True, help="summary-<ts>.csv")
    parser.add_argument("--results", type=Path, required=True, help="results-<ts>.csv")
    parser.add_argument("--perf-runs", type=Path, help="runs-<ts>.jsonl of the 8.4 run")
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = read_csv(args.summary)
    results = read_csv(args.results)
    perf = perf_from_runs(read_runs(args.perf_runs)) if args.perf_runs else None
    sources = {"Ablation summary": args.summary, "Per-question results": args.results}
    if args.perf_runs:
        sources["Performance run"] = args.perf_runs
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.md").write_text(metrics_markdown(summary, perf, sources), encoding="utf-8")
    (args.out / "ablation.svg").write_text(ablation_svg(summary), encoding="utf-8")
    (args.out / "latency.svg").write_text(latency_svg(results), encoding="utf-8")
    sys.stdout.write(f"wrote metrics.md, ablation.svg, latency.svg to {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
