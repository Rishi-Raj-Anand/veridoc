# Phase 7 — Evaluation (RAGAS + ablation)

| | |
|---|---|
| **Status** | ✅ **Complete: gate passed 2026-11-22** (full run on the team's 55-row test set; results in CLAUDE.md §17) |
| **Branch** | `feature/7-evaluation` from `main` (after `08791e6`) |
| **Touches** | `ai/` and docs only (`CLAUDE.md`, `start.md`). Backend and frontend are untouched. |
| **New dependencies** | None. `ragas==0.4.4` is already pinned in the optional `eval` group. |
| **Record** | At 7.0, copy this plan to `.claude/plans/phase_7_evaluation.md`. Fill in the results at the end. |

## Context

Phases 0–6B shipped the full pipeline: safety check, intent routing, condense, then retrieve, generate and verify with escalation and a fallback. Phase 7 measures it for the B.Tech report (CLAUDE.md §13, §17).
- **RAGAS metrics:** Faithfulness, Response Relevancy and Context Precision on a hand-written test set.
- **Pipeline stats:** verified rate, fallback rate, average iterations and latency.
- **Headline ablation:** single-pass RAG (iteration 1, no Verifier) against the full dual-agent loop.

The eval must call the **same pipeline as production**, in-process. It must fail fast if the AI service holds the Qdrant lock.

## Decisions

| Question | Decision |
|---|---|
| RAGAS judge wiring | **Your choice: an adapter over `OllamaClient`.**<br>• `OllamaRagasLLM` and `OllamaRagasEmbeddings` subclass RAGAS' base classes and call `ai_service/llm/ollama_client.py`.<br>• This keeps §6.6 intact: every Ollama call goes through one module, with `think=False` and `num_ctx=8192` guaranteed.<br>• No `langchain-ollama` dependency.<br>• §13 is updated to say this instead of "LangChain wrappers". |
| Test set | **Your choice: the team writes the rows; I build the tooling.**<br>• I ship the format, a 3-row `testset.example.jsonl` and a `--check` validator. You write 30–50 rows in `testset.jsonl`.<br>• Tooling is proven on a throwaway scratch set kept outside the repo and never reported. |
| Same pipeline as production | Move the component construction out of `main.lifespan` into a shared sync `build_runtime(settings, client)`. Both the AI service and the eval build the pipeline the same way. Production behaviour does not change. |
| Ablation variant | `build_pipeline(..., verify_drafts: bool = True)`. The eval builds the single-pass variant with `verify_drafts=False` and `settings.model_copy(update={"max_iterations": 1})`.<br>• An `ok` draft goes straight to `finalize`.<br>• A low score, `NOT_IN_DOCUMENTS` or an uncited draft goes to `fallback`.<br>• That is exactly the Phase 3 single-pass guard set.<br>• Production never passes the flag, and a test enforces this, so §7 stays untouched. Only the offline eval ever sees an unverified draft. |
| Questions per turn | `run_turn(pipeline, store=None, session_id=uuid4(), user_id=0, message=q)`. It is stateless, writes no transcripts, and reads `data/behaviour.json` like production. |
| What RAGAS scores | Only rows whose intent is `medical` and that produced an answer (status `verified`).<br>• A fallback is the correct refusal, not a bad answer. Fallback and answer rates are reported next to the metrics.<br>• A **paired subset** (rows both variants answered) is also reported, so the faithfulness comparison is like-for-like.<br>• The §7.2 emergency notice is stripped before scoring. |
| Contexts given to RAGAS | `[s.text for s in result.sources]`: the context the final draft was generated from, at the last iteration. |
| Runtime and VRAM | `RunConfig(max_workers=1)`, because one model fits in VRAM at a time (§6.8).<br>• First pass: Faithfulness and Context Precision, which use the LLM only.<br>• Second pass: Response Relevancy, which also uses `bge-m3`.<br>• This keeps model swaps down.<br>• Estimate: 40 questions × 2 variants is about 1.5–2.5 h. |
| Re-scoring without re-running | Raw pipeline outputs are appended to `data/eval/runs-<ts>.jsonl` as they arrive. `--score <runs.jsonl>` reruns RAGAS only, so a crash or a judge tweak does not cost the pipeline pass. |

---

## 7.0 Housekeeping
- `start.md` has an uncommitted change on `main`. Commit or stash it first; I will ask which.
- Create the branch and copy this plan into `.claude/plans/`.
- Run `uv sync --group eval` in `ai/`. Then **read the installed RAGAS 0.4.4 source** before writing the adapter:
  - `ragas/llms/base.py` and `ragas/embeddings/base.py`: abstract methods, `n` handling, `LLMResult` shape.
  - `ragas/metrics/`: whether the legacy `Faithfulness`, `ResponseRelevancy` and `LLMContextPrecisionWithReference` still work with `evaluate()`.
  - Contingency: if 0.4.4 only accepts the instructor-style LLM, implement its `generate(prompt, response_model)` with `OllamaClient.chat(format=response_model.model_json_schema(), temperature=0)` instead.

## 7.1 Shared wiring + ablation flag
- **New `ai_service/runtime.py`:**
  - `open_qdrant_sync(settings) -> QdrantClient`. On failure it raises `QdrantLockedError` with the existing `QDRANT_LOCK_HINT` text, moved here from `main.py`.
  - `build_runtime(settings, client, *, verify_drafts=True) -> Runtime`. `Runtime` is a dataclass holding `llm`, `vector_store`, `docstore`, `behaviour_store`, `pipeline` and `chat_store`.
  - Its body is lines 88–118 of `main.py` as they are today. On failure it closes the client.
- **`main.py` lifespan:** `open_qdrant` becomes `asyncio.to_thread(open_qdrant_sync, …)`, then `asyncio.to_thread(build_runtime, …)`, then the `app.state.*` assignments. The logs and the shutdown stay as they are.
- **`graph/pipeline.py`:**
  - `build_pipeline(..., verify_drafts: bool = True)`.
  - `after_generate` returns `"finalize"` for an `ok` draft when the flag is off.
  - Add a docstring line saying the flag is evaluation-only.

## 7.2 Test-set format + validator — `ai_service/eval/testset.py`
- **`TestCase`** (Pydantic, `extra="forbid"`):
  - `question` (1–2000 chars), `reference` (non-empty) and `source` (a filename as indexed, e.g. `disease.md`).
  - Optional `id` (defaults to the line number).
  - Optional `expect: "answer" | "fallback"`, defaulting to `answer`. A `fallback` row is an off-corpus question, `source` may be null, and the row is scored as "correctly refused?".
- **`load_testset(path)`** reports errors with line numbers and rejects duplicate questions.
- **`check_sources(cases, docstore)`**: every `source` must be a filename in `data/docstore.db`. This reads SQLite only, not Qdrant, so `--check` works **while the AI service is running**.
- **`ai_service/eval/testset.example.jsonl`**: 3 example rows from `disease.md`, marked as examples and never run by default.
- **Guidance for writing rows** (goes in the module docstring and §13):
  - Spread rows across all 5 documents.
  - Include iteration-1 facts and parent-only questions.
  - Include a few dosage questions whose dose the document states explicitly.
  - Include about 5 `expect: fallback` off-corpus questions.

## 7.3 RAGAS adapter — `ai_service/eval/ragas_adapter.py`
- **`OllamaRagasLLM`** calls `OllamaClient.chat(messages, temperature=0)`.
  - It loops for `n > 1`, because Response Relevancy asks for several generations.
  - The async methods use `asyncio.to_thread`.
  - It returns RAGAS' expected result type.
- **`OllamaRagasEmbeddings`** calls `OllamaClient.embed`.
- **Imports:** only `run_ragas.py` imports this module, and it imports `ragas` lazily. The default `uv sync`, without the eval group, must still import and test the rest of `ai_service`.

## 7.4 Runner — `ai_service/eval/run_ragas.py`
- **CLI:**
  - `--testset PATH` (default `ai_service/eval/testset.jsonl`).
  - `--variant full|single_pass|both` (default `both`).
  - `--limit N`.
  - `--check`: validate only.
  - `--score RUNS.jsonl`: score an earlier run.
  - `--no-ragas`: stats only.
- **Startup, all fail-fast with exit code 2 and a clear message:**
  - The test set loads and passes `check_sources`.
  - Ollama is reachable and both models are pulled (`list_models`).
  - `open_qdrant_sync` succeeds. On `QdrantLockedError` it prints: *"Qdrant storage at <path> is locked by another process. Qdrant local mode allows one process only — stop the AI service (Ctrl+C in its terminal) before running the evaluation."*
  - The `chunks` collection has more than 0 points (otherwise the knowledge base is empty).
- **Pipeline pass:** for each variant and each case, call `run_turn`, time it, and append a row to `runs-<ts>.jsonl`. Each row holds:
  - variant, id, question, reference, source, expect
  - intent, status, emergency, iterations, attempts
  - latency_s, best_score
  - answer (notice stripped), contexts
  - cited filenames, and `source_hit` (the expected file appears among the citations)
  - error
  - An `OllamaError` on one row is recorded and the run continues.
- **Scoring pass:**
  - Build an `EvaluationDataset` from the eligible rows: `user_input`, `response`, `retrieved_contexts` and `reference`.
  - Call `evaluate()` twice, as described in Decisions.
  - Merge the scores back by row.
- **Outputs:**
  - `data/eval/results-<YYYYMMDD-HHMM>.csv`: one row per case and variant.
  - `data/eval/summary-<ts>.csv`: one row per variant.
  - The same summary table on stdout.
  - Progress goes through `logging`.
- **Summary per variant:**
  - n, and the questions routed away from `medical` (count and list; the target is 0)
  - answered (verified) rate and fallback rate
  - correct-refusal rate on `expect: fallback` rows
  - average iterations
  - mean, median and p95 latency
  - mean Faithfulness, Response Relevancy and Context Precision, over answered rows and over the paired subset
  - source-hit rate

## 7.5 Tests (mocked LLM; default `uv run pytest` needs no RAGAS)
- **`test_eval_testset.py`:**
  - A valid file loads.
  - Malformed JSON reports its line number.
  - Missing or extra fields are rejected.
  - A duplicate question is rejected.
  - An unknown `source` is rejected against a temporary docstore.
  - An `expect: fallback` row with a null source is accepted.
- **`test_pipeline.py` additions**, for the single-pass variant:
  - An `ok` draft is finalised and the Verifier is **never called**.
  - A low score falls back with no LLM call.
  - `NOT_IN_DOCUMENTS` falls back.
  - The iteration count never exceeds 1.
- **Production guard:** `main.py`, `runtime.py` defaults and `routers/` never pass `verify_drafts=False`. This is source-scanned like the existing constants test.
- **`test_runtime.py`:**
  - Opening a temporary `vectors` directory twice raises `QdrantLockedError`, and the message contains "stop the AI service".
  - `build_runtime` returns a working pipeline with fakes.
- **`test_eval_runner.py`**, with a fake runtime and no RAGAS:
  - Rows are collected.
  - Contexts come from `sources`.
  - The notice is stripped.
  - Non-medical rows are counted.
  - Fallback rows are excluded from scoring.
  - Summary maths on hand-made rows: rates, averages, p95, paired subset.
  - The `runs.jsonl` round-trip works for `--score`.
- **`test_ragas_adapter.py`**, using `pytest.importorskip("ragas")`:
  - `n=3` gives 3 generations at temperature 0.
  - Embeddings are delegated.
- **Live test (`@pytest.mark.ollama`, skipped by default):** the adapter's Faithfulness is > 0.5 for a trivially faithful answer and < 0.5 for a contradicting one.

## 7.6 Live run (needs your test set)
1. Stop the AI service.
2. In `ai/`, run `uv sync --group eval`, then `uv run python -m ai_service.eval.run_ragas --check`.
3. Smoke test: `--limit 3 --variant both`, then look at the CSV by hand. Are the judge scores sane? Is the notice stripped?
4. Full run: `uv run python -m ai_service.eval.run_ragas` (both variants).
5. Record the ablation table, the stats and anything surprising in this plan and in CLAUDE.md under Phase 7. They feed 8.5.

## 7.7 Docs
- **CLAUDE.md:**
  - §5 layout: the `eval/` files.
  - §12 commands: `uv sync --group eval`, the flags, "stop the AI service first".
  - §13: the adapter instead of LangChain wrappers; the `expect` field; scoring answered rows plus the paired subset; the output files.
  - §17 Phase 7: ✅ marks, status and gate.
- **`start.md`:** a short "Run the evaluation" section.
- **`ai/pyproject.toml`:** update the comment on the `eval` group.

---

## Verification
- **Quality checks in `ai/`:** `uv run ruff check . ; uv run ruff format --check . ; uv run pytest` is green. Run `pytest` twice: once without the eval group, once after `uv sync --group eval` so the adapter tests run.
- **Regression:** the AI service still starts and `/api/health` is green after the `runtime.py` refactor. One chat turn through the backend still returns `verified`.
- **Lock check:** with the AI service running, `run_ragas` exits 2 with the "stop the AI service" message. `--check` still works.
- **Gate:**
  - `--check` passes on 30–50 team-written rows.
  - Both variants complete and both CSVs are written.
  - No test-set question is routed away from `medical`, or each one is listed and explained.
  - The ablation table is recorded in CLAUDE.md.
  - The §12 checks are green.

## Critical files
- New: `ai/ai_service/runtime.py`, `ai/ai_service/eval/{__init__,testset,ragas_adapter,run_ragas}.py`, `eval/testset.example.jsonl`, and the tests above.
- Changed: `ai/ai_service/main.py` (uses `runtime.py`), `ai/ai_service/graph/pipeline.py` (`verify_drafts`), `CLAUDE.md`, `start.md`, the comment in `ai/pyproject.toml`.
- Reused: `run_turn`/`build_pipeline` (`graph/pipeline.py`), `OllamaClient.chat/embed/list_models` (`llm/ollama_client.py`), `Docstore` (`retrieval/docstore.py`), `EMERGENCY_NOTICE` and `Settings.eval_dir` (`config.py`), `FakeLlm`/`FakeQdrant` (`tests/helpers.py`).

---

## Results (2026-11-22)

### Built
| Task | Result |
|---|---|
| 7.0 | `start.md` committed on `main` (`08791e6`); branch created; `uv sync --group eval` installed RAGAS 0.4.4; its source was read before writing the adapter |
| 7.1 | `runtime.py` (`open_qdrant_sync`, `build_runtime`, `QdrantOpenError`); `main.py` now uses it with unchanged behaviour; `build_pipeline(verify_drafts=...)` |
| 7.2 | `eval/testset.py` + `testset.example.jsonl`; `Docstore.filenames()` for `--check` |
| 7.3 | `eval/ragas_adapter.py`: `OllamaRagasLLM` (`BaseRagasLLM`) and `OllamaRagasEmbeddings` (`BaseRagasEmbeddings`). The legacy metrics still work with `evaluate()` in 0.4.4, so the instructor contingency was not needed |
| 7.4 | `eval/run_ragas.py`; `VectorStore.count()` for the empty-knowledge-base check |
| 7.5 | AI tests 402 → **446** (+1 live behind `-m ollama`). Without the eval group: 439 passed, 1 module skipped |
| 7.7 | CLAUDE.md §3, §5, §12, §13 and §17; `start.md`; the comment in `pyproject.toml` |

### Deviations from the plan
- **The lock error is `QdrantOpenError`**, not `QdrantLockedError`. Qdrant can fail to open for reasons other than a lock. The runner turns it into the "stop the AI service" message.
- **Two RAGAS hazards turned up in its source and are now guarded:**
  - **Telemetry.** RAGAS sends usage analytics unless `RAGAS_DO_NOT_TRACK=true`, and it caches that flag on first use. `ai_service/eval/__init__.py` sets it before any module can import `ragas`, and a test checks it.
  - **Silent cloud fallback.** `evaluate()` falls back to OpenAI `gpt-4o-mini` for any metric with no LLM. `build_metrics` attaches our judge to every metric and raises if one is missing, and a test checks this.
- **Response Relevancy temperature.** It asks for n=3 completions. Those use the temperature RAGAS requests (0.3); every single completion runs at 0. At temperature 0 the three generated questions would be identical.
- **The embeddings are not passed to `evaluate()`.** The metrics already hold them, and passing them again only made RAGAS re-wrap them in its deprecated LangChain wrapper.
- **The single-pass variant records an `unverified` attempt outcome**, so its transcripts and CSV rows show the audit was skipped.
- **Average iterations now covers every medical row**, off-corpus ones included, because their escalations are real cost.
- **`score_rows` assigns a fresh `scores` dict** instead of mutating one in place. The adapter test found aliasing through a shallow `model_copy`.

### Verified live (real `qwen3:8b` + `bge-m3`, real knowledge base of 5 documents)
- **Live judge test:** a faithful answer scored above 0.5 and a contradicting one below 0.5 (43 s).
- **Smoke run** on `testset.example.jsonl` (3 rows, both variants, full RAGAS) took ~4 min and ended with exit 0. Outputs were moved to the scratchpad, so `data/eval/` holds no smoke files.
  - Both in-corpus questions were answered by both variants, about 15–19 s per question.
  - The off-corpus LASIK question fell back in both variants. `full` spent 3 iterations (`not_covered` ×2, then `uncited`); `single_pass` spent 1.
  - Faithfulness 1.00, Context Precision 1.00, Response Relevancy 0.60–0.78. RAGAS scoring took about 1 min per pass for 4 answers.
  - The dengue answer cited `standard-treatment-guidelines.pdf` rather than `disease.md`. This is correct, and it shows that "cites the expected source" is a heuristic.
  - Three questions cannot show the ablation gap; the team's 30–50 rows will.
- **Regression:** the AI service starts after the `runtime.py` refactor, `/internal/health` reports `ok`, and an `/internal/chat` turn returned `verified` with a citation. The test transcript was deleted afterwards.
- **Lock:** with the service running, `run_ragas` exits 2 with "…stop the AI service (Ctrl+C in its terminal)…" and writes nothing. `--check` still works.

### Full run (gate), 2026-11-22
- **Test set:** 55 rows from the team's CSV, converted to `ai/ai_service/eval/testset.jsonl` (10 per document + 5 off-corpus). Sources were mapped to the indexed filenames, and `--check` passed.
- **Run:** both variants, exit 0, 0 errors. About 30 min for the 110 pipeline turns and about 70 min for RAGAS (one judge call hit the 10-minute timeout).
- **Outputs:** `data/eval/results-20261122-0845.csv`, `summary-20261122-0845.csv`, `runs-20261122-0845.jsonl` (git-ignored).

| | Full loop | Single-pass |
|---|---|---|
| Answered (in-corpus) | 96% | 90% |
| Faithfulness (all / paired) | 0.97 / 0.96 | 0.92 / 0.92 |
| Answer Relevancy | 0.88 | 0.88 |
| Context Precision | 0.93 | 0.94 |
| Faithfulness < 0.5 | 0 | 2 |
| Avg iterations | 1.30 | 1.00 |
| Latency mean / p95 | 17.2 / 36.6 s | 13.7 / 16.1 s |

The Verifier turned 3 unfaithful single-pass answers into faithful ones and recovered 3 more questions the single pass could not cite, for about 3.5 s extra per answer. `medquad-05` and `medquad-10` were routed to `out_of_scope` by the classifier in both variants (reported, not changed). Full reading and caveats are in CLAUDE.md §17.
