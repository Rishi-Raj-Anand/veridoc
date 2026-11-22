# Report assets

Figures and tables for the B.Tech project report. All of them are generated or drawn from
this repository; none contains real user data.

| Asset | What it shows | How it was made |
|---|---|---|
| [`architecture.svg`](architecture.svg) | The four processes, their ports, data stores and boundaries | Hand-written SVG (CLAUDE.md §4) |
| [`pipeline.svg`](pipeline.svg) | The LangGraph query pipeline: safety check, intent routing, the verify/escalate loop, fallback | Hand-written SVG (CLAUDE.md §6.2) |
| [`metrics.md`](metrics.md) | Ablation table (full loop vs single pass), pipeline stats, per-iteration latency and prompt sizes | `ai_service/eval/report.py` |
| [`ablation.svg`](ablation.svg) | Answered rate, Faithfulness, Answer Relevancy and Context Precision for both variants | `ai_service/eval/report.py` |
| [`latency.svg`](latency.svg) | Every question's latency per variant, with mean and p95 | `ai_service/eval/report.py` |
| [`screenshots/`](screenshots/) | The app at desktop (1280 px) and phone (390 px) width | Playwright + Edge against a throwaway stack (scratch data, demo admin, `disease.md`) |

The SVGs follow the viewer's light or dark setting. To place one in a Word or LaTeX report,
open it in a browser and print or screenshot it, or convert it with any SVG tool.

## Data sources

- **Ablation** (`metrics.md` tables 1, `ablation.svg`, `latency.svg`): the Phase 7 run of
  2026-11-22, `data/eval/summary-20261122-0845.csv` and `results-20261122-0845.csv`.
  55 questions × 2 variants, RAGAS 0.4.4 with the local `qwen3:8b` as judge.
- **Performance** (`metrics.md`, last section): the Phase 8.4 run of 2026-11-22,
  `data/eval/runs-20261122-1111.jsonl`: the full loop on the same 55 questions, without RAGAS.

`data/` is git-ignored, so these inputs stay on the machine that ran them. To rebuild:

```powershell
cd ai
uv run python -m ai_service.eval.report --summary ..\data\eval\summary-20261122-0845.csv --results ..\data\eval\results-20261122-0845.csv --perf-runs ..\data\eval\runs-20261122-1111.jsonl --out ..\docs\report
```

## Screenshots

| File | Page |
|---|---|
| `home-*.png` | Landing page (`/home`) |
| `chat-verified-*.png` | A verified answer with its citation and the disclaimer |
| `chat-emergency-*.png` | A red-flag message: the emergency notice above the answer |
| `chat-smalltalk-refusal-*.png` | A greeting and an off-topic request (the admin's refusal text) |
| `admin-documents-*.png` | Admin: system status, upload, the document list |
| `admin-behaviour-*.png` | Admin: tone, scope, refusal text, guardrails and the locked safety rules |
| `not-found-*.png` | The 404 "case file" page |
| `status-card-ok.png` | System status with every service up |
| `status-card-empty-kb.png` | No documents indexed |
| `status-card-offline.png` | Ollama not reachable, with the fix |
| `status-card-missing.png` | A model not pulled, with the `ollama pull` command |

The model-not-pulled card used a deliberately wrong `LLM_MODEL` (`qwen3:doesnotexist`), and the
offline card an unused Ollama port, on the throwaway stack only.
