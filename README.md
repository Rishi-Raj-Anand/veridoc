# VeriDoc AI

A self-correcting **dual-agent RAG** system. A **Generator** drafts answers from retrieved
document chunks; a **Verifier** audits each draft for faithfulness and relevance. If a draft
fails, retrieval is escalated (wider Top-K, then parent chunks) for up to 3 iterations, after
which the system returns a deterministic safe fallback instead of guessing.

**v1 — "Doctor AI":** an admin uploads medical reference documents; users chat with an
assistant that answers **only** from those documents, with citations. All AI runs locally
through Ollama (`qwen3:8b` + `bge-m3`) — no cloud LLM or embedding APIs.

B.Tech CSE Major Project — Rishi Raj Anand (23103121), Vedant Salve (23117040).
Mentor: Dr. Jagdeep Kaur.

> ⚠️ VeriDoc AI provides information from reference documents and is not a substitute for
> professional medical advice, diagnosis, or treatment.

## What it does

- **Grounded, cited answers.** Every medical statement comes from an uploaded document and
  carries a `[n]` citation (file and page). No answers from the model's general knowledge.
- **Verify, then escalate.** The Verifier scores each draft. A failed draft triggers a wider
  search (Top-3 chunks → Top-8 → parents of Top-15), at most 3 times, then a fixed fallback.
- **Safety first.** Red-flag emergencies (chest pain, stroke signs, overdose…) are detected by
  rules and get an emergency notice. Doses are stated only when a document states them.
- **Intent routing.** Greetings get a short friendly reply; off-topic requests (recipes,
  code, homework) get the admin's refusal text. Neither touches the medical pipeline.
- **Session memory.** Follow-ups such as "what about for children?" are rewritten into a
  standalone question. Every visit starts a fresh session.
- **Admin page.** Upload PDF / TXT / MD / DOCX / CSV, watch indexing status, delete documents,
  tune the assistant's tone and guardrails, and see the health of every service.

## Results (RAGAS, 55-question test set)

Full dual-agent loop against single-pass RAG (iteration 1, no Verifier), both on the same
pipeline and the same local models. Details and caveats: `CLAUDE.md` §17, Phase 7.

| | Full loop | Single-pass |
|---|---|---|
| In-corpus questions answered | **96%** | 90% |
| Off-corpus questions refused | 100% | 100% |
| Faithfulness | **0.97** | 0.92 |
| Answers with Faithfulness < 0.5 | **0** | 2 |
| Answer Relevancy / Context Precision | 0.88 / 0.93 | 0.88 / 0.94 |
| Latency mean / p95 | 17.2 / 36.6 s | 13.7 / 16.1 s |

The judge is a local 8B model, so compare the columns relatively. Charts, tables, diagrams
and screenshots for the report are in [`docs/report/`](docs/report/).

## Architecture

```
frontend (React, :5173)  ->  backend (FastAPI, :8000)  ->  ai service (FastAPI, 127.0.0.1:8001)
                                    |                              |
                              SQLite app.db                 Qdrant + docstore
                                                                   |
                                                       Ollama (:11434) qwen3:8b, bge-m3
```

The frontend talks only to the backend. The backend owns auth, users and uploads. The AI
service owns parsing, embeddings, Qdrant and the LangGraph agent pipeline.

## Getting started

Everything runs locally on Windows. **[`start.md`](start.md)** has the full guide:
prerequisites, first-time setup, starting the four processes (or all at once), health
checks, the demo admin login and troubleshooting.

Target hardware: an 8 GB laptop GPU (RTX 5050), 16 GB RAM. `qwen3:8b` and `bge-m3` cannot
both stay in 8 GB of VRAM, so Ollama swaps them (~7 s each time). A medical answer takes
about 16 s (median), and under ~50 s when all three iterations run; small talk and refusals
take about a second. Measurements: [`docs/report/metrics.md`](docs/report/metrics.md).

## Quality checks

```powershell
.\check.ps1              # ruff, pytest, ESLint, tsc, Prettier, Vitest and a production build
.\check.ps1 -Only ai     # one service: ai, backend or frontend
.\check.ps1 -Live        # also the AI tests that need a running Ollama
```

## Documentation

| File | What it holds |
|---|---|
| [`start.md`](start.md) | Setup, start, check, stop, troubleshoot, project structure |
| [`CLAUDE.md`](CLAUDE.md) | The spec: architecture (§4), AI pipeline (§6), medical safety rules (§7), API contracts (§9), environment variables (§11), commands (§12), evaluation (§13), task breakdown and results (§17) |
| [`docs/report/`](docs/report/) | Report assets: diagrams, metric tables, charts, screenshots |
| `.claude/plans/` | Per-phase plans with recorded test and live-gate results |
