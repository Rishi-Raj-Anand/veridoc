# Phase 8 — Hardening (M9)

| | |
|---|---|
| **Status** | ✅ **Complete: gate passed 2026-11-22** (results at the end) |
| **Branch** | `feature/8-hardening` from `main` (after `7af0908`); merged to `main` 2026-11-22 (merge `c33655c`, feature commit `e929276`), then deleted |
| **Touches** | all three services, docs, a new root `check.ps1`, a new `docs/report/` folder |
| **New dependencies** | None. The report script is standard library only. Playwright for screenshots stays outside the repo, as in Phase 6. |
| **New env variables** | None. Timeouts stay as module constants. All three `.env.example` files already match their `Settings` exactly (checked). |
| **Record** | At 8.0, copy this plan to `.claude/plans/phase_8_hardening.md`. Fill in the results at the end. |

## Context

Phases 0–7 shipped and measured the full pipeline. Phase 8 (CLAUDE.md §17) hardens it for the demo and the report:
- every failure gives a specific, friendly message;
- the test gaps are filled, and one script runs all the checks;
- the docs are brought up to date;
- latency and the prompt budget are measured per iteration;
- the report assets are produced.

The exploration found these gaps:
- **One cause, one message.** Chat 503s all collapse to "Doctor AI is unavailable" (`backend/app/routers/chat.py:22`). "Ollama down", "model not pulled" and "timeout" look the same to users.
- **Stuck Ollama calls.** A stuck call can block a turn for about 15 minutes: 3 tries × 300 s, because `ReadTimeout` is retried (`ollama_client.py:59-62`). The backend gives up after 300 s.
- **Empty knowledge base.** It still embeds the query, then silently falls back. Nobody is told why.
- **Uploads.** The whole multipart body is spooled before the 25 MB check runs.
- **Unhandled 500s.** The backend has no catch-all handler. An unhandled 500 is plain text without CORS headers, so the browser reports it as "could not reach".
- **Frontend.**
  - No fetch timeout.
  - A raw `TypeError` reaches the pages.
  - TanStack retries 4xx three times.
  - Nothing checks file size before uploading.
  - `HealthBanner` hides `ollama.error` / `qdrant.error` and has no tests.
- **Error text.**
  - An AI 422 with a list `detail` becomes "unexpected error".
  - A health `ReadTimeout` is mislabelled "unreachable", because both sides use a 5 s timeout.
  - Ingest and delete 500s leak raw exception text.
- **Logging.** Latency is logged per turn only, and the prompt-token total per turn is never logged.
- **Tooling.** There is no quality-check script.

## Decisions

| Question | Decision |
|---|---|
| Empty knowledge base | **Your choice: admin side only.** Users keep the exact §7.4 fallback. The AI service skips the embed, logs a warning, and health reports the chunk count. The admin card warns. |
| Report assets | **Your choice: a committed `docs/report/` folder.** It holds hand-written SVG diagrams and a stdlib script that builds the tables and charts from `data/eval/*.csv`. Screenshots come from Playwright. |
| Telling causes apart | AI-service 503s carry an **`X-Error-Code`** header: `ollama_unreachable`, `model_missing` or `ollama_timeout`.<br>• The `{"detail": "..."}` body is unchanged, so the §9 contract only gains a field.<br>• The backend adds `ai_unreachable` and `ai_timeout` itself.<br>• One code → user-message map lives in the backend. Admins still get the technical text through health and the failed-row `error`. |
| Timeouts | Ollama per-call timeout drops from 300 to **120 s**, and **read timeouts are no longer retried** (re-asking a stuck generation only triples the wait). Connect errors and 5xx are still retried. So one hang surfaces in about 120 s, well inside the backend's 300 s. The eval passes its own longer timeout. Frontend timeouts: chat **330 s** (above the backend's 300 s), uploads 300 s, everything else 30 s. |
| Health timing | AI `list_models` goes from 5 → **3 s**; the backend health call goes from 5 → **8 s**. A hung Ollama then reads "Ollama down", not "AI service unreachable". |
| Check script | One root **`check.ps1`** (PowerShell 5.1-safe). Added to §5. |

### What each failure looks like after Phase 8

| Failure | User in chat | Admin |
|---|---|---|
| AI service down | "Doctor AI is unavailable right now. Please try again in a moment." (unchanged) | Status card: AI service unreachable (unchanged) |
| Ollama down / model not pulled | "Doctor AI's language model is offline right now. Please try again later." | Status card shows `ollama.error` or "missing `qwen3:8b` — run `ollama pull qwen3:8b`". A failed ingest row shows the same text. |
| Ollama or AI timeout | "Doctor AI took too long to answer. Please try again." | Logged with the cause |
| Empty knowledge base | the §7.4 fallback, unchanged | Amber note: "No documents indexed — Doctor AI will refuse every medical question." Plus a log warning. |
| Upload over `MAX_UPLOAD_MB` | — | Rejected in the browser before sending. The server returns 413 from `Content-Length` before reading the body. The streaming check stays as the authority. |
| Backend down | "Could not reach the VeriDoc server. Check that it is running and try again." (one message from `client.ts`) | Status card: "Backend unreachable" (network errors only) |
| Unhandled backend error | "Something went wrong on the server. Please try again." (JSON 500, with CORS headers) | Traceback in the backend log |

---

## 8.0 Housekeeping
- Create the branch.
- Copy this plan to `.claude/plans/phase_8_hardening.md`.

## 8.1 Error-handling sweep

**AI service (`ai/ai_service/`)**
- **`llm/ollama_client.py`:**
  - Add `OllamaUnreachableError`, `ModelNotPulledError` and `OllamaTimeoutError`. All three subclass `OllamaError`, and each has a `code` attribute.
  - `httpx.TimeoutException` becomes `OllamaTimeoutError`, with no retry.
  - `OllamaClient(settings, *, request_timeout=REQUEST_TIMEOUT_SECONDS)`, where the default is 120. `eval/run_ragas.py:651` passes `JUDGE_TIMEOUT_SECONDS`.
  - `LIST_TIMEOUT_SECONDS = 3`.
- **`routers/chat.py`, `routers/ingest.py`:** an `OllamaError` becomes 503, with `headers={"X-Error-Code": exc.code}` (`ollama_error` for the base class).
  - **Ingest:**
    - A PDF page-extraction failure and a file-read `OSError` become `ParseError` → 422 with plain text ("could not read page N of the PDF", "could not read the uploaded file").
    - Any other exception is logged with its traceback. It returns 500 `"indexing failed unexpectedly (<ExcType>) - see the AI service log"`, no longer the raw exception text.
  - **`DELETE /internal/documents/{id}`:** wrapped the same way.
- **Empty knowledge base:**
  - The `retrieve` node checks `vector_store.count() == 0` before embedding. If it is empty, it goes to `fallback` with the attempt outcome `empty_kb` and logs a warning: "knowledge base is empty - upload documents on the admin page". There is no `bge-m3` swap.
  - `/internal/health` gains `knowledge_base: {chunks: int | null}`. The value is `null` if Qdrant is not open. The status is not changed by it.
- **Startup (`main.py` lifespan):** one `list_models` probe in a thread. It only logs warnings ("Ollama not reachable at … - chat and ingest fail until it is started", "model X not pulled - run: ollama pull X") and never blocks startup.

**Backend (`backend/app/`)**
- **`services/ai_client.py`:**
  - `AiServiceError` gains `code`. It is taken from `X-Error-Code`, or set to `ai_unreachable` / `ai_timeout` locally.
  - `ConnectTimeout` goes explicitly to unreachable.
  - A list `detail` is joined from its `msg` fields, the same rule as the frontend's `errorDetail`.
  - `fetch_health`: timeout 8 s. A `ReadTimeout` reads "AI service did not answer the health check within 8 s".
- **`routers/chat.py`:** a `USER_MESSAGES` code → text map (the table above). The technical reason is still logged.
- **`main.py`:**
  - A pure-ASGI `UploadSizeLimit` middleware for `POST /api/admin/documents`. A `Content-Length` above `max_upload_bytes` + 1 MB of multipart slack gets 413 with the existing "file is larger than the N MB upload limit" text, before the body is read.
  - A catch-all middleware, added **before** `CORSMiddleware` so CORS wraps it. It logs the exception and returns JSON 500.
  - Check the middleware order with a test that asserts `access-control-allow-origin` on the 500.
- **`config.py`:** a startup warning when `JWT_SECRET` is under 32 bytes. Today `start.md` says "must" and nothing checks it. `MAX_UPLOAD_MB` gets `gt=0`.
- **`schemas/health.py`:** optional `knowledge_base`.

**Frontend (`frontend/src/`)**
- **`api/client.ts`:**
  - A rejected `fetch` becomes `ApiError(0, "Could not reach the VeriDoc server…")`.
  - An `AbortController` timeout produces `ApiError(0, <per-call timeout text>)`.
  - Default 30 s. `chat.ts` passes 330 s and "Doctor AI took too long to answer…". `admin.ts` upload passes 300 s.
  - The per-page "Could not reach…" fallbacks in `ChatPage`, `AdminPage`, `AdminBehaviourPage`, `LoginPage` and `RegisterPage` collapse into this one message.
- **`main.tsx`:** the `QueryClient` default `retry` is now: never for 4xx, at most once otherwise.
- **`constants.ts`:** `MAX_UPLOAD_MB = 25`, mirroring the backend default like `DISCLAIMER` does. `AdminPage` rejects a larger file before uploading. `DocumentTable`'s empty-state text adds `.csv`.
- **`components/HealthBanner.tsx`:**
  - Show `ollama.error`, `qdrant.error` and missing models with the pull command.
  - Show the empty-knowledge-base warning.
  - Say "Backend unreachable" only when the status is 0.
  - `api/health.ts` types gain `knowledge_base`.

## 8.2 Test gaps + `check.ps1`

**New tests (mocked; the default runs need no Ollama)**
- **AI:**
  - Each new `OllamaError` subclass and its code.
  - A read timeout is not retried; 5xx gives up after the last try.
  - `list_models` on failure.
  - Chat over HTTP:
    - 503 with `X-Error-Code` for model-missing, unreachable and timeout (using the existing `FakeLlm.chat_fails`);
    - 500 "unexpected";
    - empty KB → fallback with no embed call.
  - Ingest: a bad DOCX, a password-protected PDF, a page-extraction error → 422, a CSV `ParseError` over HTTP, the generic 500 text.
  - Delete storage failure.
  - Health with only `qwen3` missing, only `bge-m3` missing, and `knowledge_base`.
  - The startup probe logs, without lifespan: call the probe function directly.
- **Backend:**
  - `ConnectTimeout`.
  - A list `detail`.
  - `X-Error-Code` → each user message.
  - Behaviour `_call` timeout and connect failures.
  - Health `ReadTimeout` and "Ollama down".
  - `run_ingest` through the real `_call` mapping (`httpx.MockTransport`).
  - Early 413 from `Content-Length`.
  - JSON 500 with CORS.
  - The `JWT_SECRET` warning.
- **Frontend:**
  - `client.ts`: network failure, timeout and a non-JSON error body.
  - A new `HealthBanner.test.tsx` (ok, degraded, AI down, missing models, empty KB, backend unreachable).
  - `AdminPage`: oversized file rejected client-side, 413/415 shown, delete 503/409, list error.
  - `ChatPage`: network failure, timeout text, 401 mid-session.
  - `RegisterPage` 409.
  - `AdminBehaviourPage` save 503.

**`check.ps1` (repo root)**
- Runs, in order:
  - `ai`: ruff check, ruff format `--check`, pytest
  - `backend`: the same three
  - `frontend`: `lint`, `typecheck`, `format:check`, `test`, `build`
- Switches: `-Only ai|backend|frontend` and `-Live`. `-Live` adds `uv run pytest -m ollama` in `ai/`.
- `Push-Location`/`Pop-Location` per folder, so it works from any working directory.
- It checks `$LASTEXITCODE` after each step and keeps going after a failure.
- It ends with a PASS/FAIL table and `exit 1` if any step failed.
- No `&&`, so it runs in PowerShell 5.1.

## 8.3 Docs
- **`README.md`:** rewrite it as the project front page.
  - What it is, and the features (dual-agent loop, intent routing, safety rules).
  - Status: all phases done.
  - The ablation headline table from §17.
  - Quick start → `start.md`.
  - `.\check.ps1`.
  - Where the docs and report assets live.
  - Keep the setup detail in `start.md` only; drop the duplicate run block.
- **`start.md`:**
  - `.\check.ps1` replaces the three-line block.
  - New troubleshooting rows: "language model is offline", "took too long", "No documents indexed", the 413 and client-side size rejections.
  - `check.ps1` and `docs/report/` in the folder tree.
- **`CLAUDE.md`:**
  - §5: `check.ps1`, `docs/report/`, `eval/report.py`.
  - §6.7: the attempt `seconds` and the assistant entry's `llm_calls`.
  - §9: the `X-Error-Code` header and the health `knowledge_base` field.
  - §12: `check.ps1`.
  - §17 Phase 8: ✅ marks, status and gate.
- **`.env.example` files:** re-check that they match `Settings` (no new variables are planned).

## 8.4 Performance instrumentation + live check

**Instrumentation (AI)**
- **`llm/ollama_client.py`:**
  - A `contextvars`-based `llm_usage()` scope. While it is open, every `chat`/`embed` appends `{purpose, model, prompt_tokens, completion_tokens, seconds}`.
  - `chat(..., purpose=...)` is passed by each agent: `classify`, `smalltalk`, `condense`, `generate`, `verify`, `judge`.
  - `asyncio.to_thread` copies contextvars, so this works under the router.
- **`graph/pipeline.py`:**
  - Every `Attempt` gains `seconds`, from the start of `retrieve` to the end of that iteration's guard or verify.
  - `run_turn` opens the usage scope and logs `max_prompt_tokens=`.
  - `PipelineResult` and the transcript's assistant entry carry `llm_calls`, which is stored and never shown.
  - The chat router logs the `chat_lock` wait time.
- **Eval:**
  - `run_ragas` rows gain `max_prompt_tokens` (attempts already flow through with `seconds`).
  - The summary adds mean seconds per iteration level and the max prompt tokens.
- **Budget test:**
  - A static worst case for the Generator and the Verifier prompts, using the `len/4` estimator:
    - the system prompt;
    - the admin text at its caps (Generator only);
    - `CONTEXT_MAX_TOKENS` of sources;
    - a 2,000-character question;
    - a 1,000-token draft (Verifier only).
  - It must be under 7,000 tokens.
  - If the Verifier exceeds it, **stop and ask** before changing any trimming. It touches medical output.

**Live check (real `data/`, AI service stopped; the run writes only to `data/eval/`)**
1. Run `uv run python -m ai_service.eval.run_ragas --variant full --no-ragas` on the 55-row set (~15 min).
   - Sample `ollama ps` during the run. Both models should be at 100% GPU.
2. Record:
   - latency per iteration level (mean and p95);
   - turn latency;
   - the swap overhead;
   - max and p95 prompt tokens per purpose against the ~7,000 budget;
   - the worst-case 3-iteration turns, which come from the off-corpus rows.
3. Record the table in this plan and in CLAUDE.md §17. Keep the runs file for 8.5.

## 8.5 Report assets — `docs/report/`
- **Diagrams:** `architecture.svg` (§4 services) and `pipeline.svg` (§6.2 graph). Both are hand-written SVG and readable in light or dark.
- **`ai/ai_service/eval/report.py`:** stdlib only.
  - Command: `uv run python -m ai_service.eval.report --summary … --results … --runs … --out ..\docs\report`.
  - It writes:
    - `metrics.md`: the ablation table, pipeline stats, refusal rate and the 8.4 performance table;
    - `ablation.svg`: grouped bars for answered rate, Faithfulness, Relevancy and Context Precision, full loop vs single pass;
    - `latency.svg`: the latency distribution per variant.
  - Load the `dataviz` skill before writing the chart code.
- **`screenshots/*.png`:** Playwright (outside the repo) against a throwaway stack (ports 18000/18001/15173, scratch `DATA_DIR`, the demo admin only).
  - Pages: home, a verified answer with citations, the emergency banner, small talk and a refusal, the admin documents page, the behaviour page, the status card, and the 404 page.
  - At desktop and 390 px widths.
- **`docs/report/README.md`:** an index of the assets saying how each one was made.
- **Tests:** a test for `report.py`'s table and SVG output on hand-made CSV rows.

---

## Verification
- **Checks:** `.\check.ps1` is green (ai, backend, frontend: lint, format, types, tests, build). `.\check.ps1 -Live -Only ai` is green with Ollama running.
- **Live 8.1 walk-through** on a throwaway stack (scratch `DATA_DIR`, ports 18000/18001/15173, real models), in a browser:
  - Ollama stopped → chat shows "language model is offline", and the card shows the Ollama error.
  - `LLM_MODEL=qwen3:doesnotexist` in the scratch `ai` env → the same chat text, and the card shows the pull command.
  - No documents → a medical question gets the §7.4 fallback with no `bge-m3` load (`ollama ps`), and the card shows the amber note.
  - A 30 MB file → rejected in the browser. `curl` with that `Content-Length` → an immediate 413.
  - AI service stopped → "Doctor AI is unavailable". Backend stopped → "Could not reach the VeriDoc server", and the card says "Backend unreachable".
  - Regression: an in-corpus question is still `verified` with citations, and small talk and refusals are unchanged.
- **8.4:** the performance table is recorded, `ollama ps` shows 100% GPU, and the max prompt tokens are under 7,000.
- **Gate:** all of the above, `docs/report/` committed with the diagrams, tables, charts and screenshots, the docs updated, and the results filled in here and in CLAUDE.md §17.

## Critical files
- **Changed in AI:** `llm/ollama_client.py`, `routers/{chat,ingest,health}.py`, `graph/pipeline.py` (+ `state.py`), `ingestion/parsers.py`, `main.py`, `memory/chat_store.py` (`llm_calls`), the agents (`purpose=`), `eval/run_ragas.py`.
- **Changed in backend:** `services/ai_client.py`, `routers/chat.py`, `main.py`, `config.py`, `schemas/health.py`.
- **Changed in frontend:** `api/{client,chat,admin,health}.ts`, `main.tsx`, `constants.ts`, `components/HealthBanner.tsx`, `components/admin/DocumentTable.tsx`, the five pages' error fallbacks.
- **New:** `check.ps1`, `docs/report/*`, `ai/ai_service/eval/report.py`, `frontend/src/components/HealthBanner.test.tsx`, and the tests above.
- **Reused:**
  - `VectorStore.count()` (`retrieval/vector_store.py:135`)
  - `trim_sources` (`agents/budget.py:24`)
  - `FakeLlm.chat_fails` / `embed_fails` (`ai/tests/helpers.py`)
  - `errorDetail` (`frontend/src/api/client.ts:55`)
  - `insecure_defaults` (`backend/app/config.py:85`)
  - `ingest_timeout_message` (`ai_client.py:117`)
  - the Phase 6 Playwright walkthrough setup

---

## Results (2026-11-22)

### Built
| Task | Result |
|---|---|
| 8.0 | Branch `feature/8-hardening`; this plan copied here |
| 8.1 AI | `OllamaUnreachableError` / `ModelNotPulledError` / `OllamaTimeoutError` with `code`; read timeouts not retried; 120 s per call (eval 600 s); `routers/errors.py` adds `X-Error-Code`; ingest/delete 500s say "see the AI service log" instead of raw text; PDF page and file-read errors are 422s; `Retriever.is_empty()` → `empty_kb` fallback with no embed; health `knowledge_base.chunks`; startup Ollama probe (warnings only) |
| 8.1 backend | `AiServiceError.code`; `USER_MESSAGES` in `routers/chat.py`; list `detail` joined; `ConnectTimeout` = unreachable; health 8 s with its own timeout text; `app/middleware.py` (`UploadSizeLimit`, `CatchAllErrors`, both inside CORS); short `JWT_SECRET` warning; `MAX_UPLOAD_MB >= 1`; `GET /api/admin/documents/limits` |
| 8.1 frontend | `client.ts`: `NO_RESPONSE`, one network message, per-call timeouts (30 s / chat 330 s / upload 300 s), `errorMessage()` used by all five pages; `queryClient.ts` (`shouldRetry`: never a 4xx, else once); `HealthBanner` shows Ollama/Qdrant errors, pull commands, the empty-KB warning, "Backend unreachable" only for status 0; upload size checked against the backend's limit; `.csv` in the empty-state text |
| 8.2 | `check.ps1` (`-Only`, `-Live`). New tests: AI `test_error_paths.py` (20), `test_prompt_budget.py` (2), `test_eval_report.py` (4) and additions to the Ollama client, pipeline and eval-runner tests; backend `test_error_handling.py` (31); frontend `client.errors.test.ts`, `HealthBanner.test.tsx`, `errorPaths.test.tsx` |
| 8.3 | README rewritten as the front page; `start.md` (check script, 7 troubleshooting rows, tree); CLAUDE.md §5, §6.7, §9, §12, §17. `.env.example` files already matched `Settings`; no new variables |
| 8.4 | `llm_usage()` scope + `purpose=` on every call; attempt `seconds`; `llm_calls` in results and transcripts; `max_prompt_tokens` in the turn log; eval rows and summary gain per-iteration seconds and max prompt tokens; lock-wait log |
| 8.5 | `docs/report/`: `architecture.svg`, `pipeline.svg`, `metrics.md`, `ablation.svg`, `latency.svg` (via `eval/report.py`, stdlib only), 18 screenshots, `README.md` |

### Deviations from the plan
- **Upload limit in the browser comes from the backend, not a constant.** The live run found `MAX_UPLOAD_MB=35` in this machine's `backend/.env`; a hard-coded 25 would have refused files the server accepts. Added `GET /api/admin/documents/limits` (admin only, additive to §9); `MAX_UPLOAD_MB` was removed from `constants.ts`.
- **"Ollama down" was simulated with a dead `OLLAMA_HOST` port** on the throwaway AI service instead of stopping your Ollama. The code path is the same (`ConnectionError` → `ollama_unreachable`).
- **`EVAL_REQUEST_TIMEOUT_SECONDS` lives in `run_ragas.py`**, because importing `JUDGE_TIMEOUT_SECONDS` from `ragas_adapter` would import `ragas` in the default install.
- **An unexpected non-connection exception from the Ollama client is now a plain `OllamaError`** ("Ollama chat failed (ValueError: …)"), not "cannot reach Ollama", and it is not retried.
- **`ollama ps` between swaps lists nothing.** Sampling during the run caught both models at 100% GPU; a note was added to §12.

### Verified live
- **Performance run** (`run_ragas --variant full --no-ragas`, 55 rows, AI service stopped, real `data/`): exit 0, 0 errors, ~17 min. Output `data/eval/runs|results|summary-20261122-1111.*` (git-ignored, kept for `report.py`).

  | Latency (s) | n | Mean | Median | p95 |
  |---|---|---|---|---|
  | Whole turn | 55 | 17.6 | 16.0 | 38.6 |
  | Iteration 1 | 53 | 15.6 | 15.5 | 18.6 |
  | Iteration 2 | 11 | 9.2 | 4.9 | 23.3 |
  | Iteration 3 | 6 | 1.6 | 1.0 | 4.5 |
  | Turns that used all 3 iterations | 6 | 20.8 | 15.9 | 47.0 |

  | Prompt tokens (Ollama) | Calls | Mean | p95 | Max |
  |---|---|---|---|---|
  | classify | 54 | 424 | 434 | 436 |
  | generate | 70 | 1,817 | 4,573 | 5,090 |
  | verify | 53 | 1,661 | 3,538 | 4,638 |

  - Answered 94% of in-corpus questions this run (Phase 7: 96%); temperature-0.2 generation varies a little from run to run.
  - `ollama ps`: `qwen3:8b` 6.2 GB and `bge-m3` 689 MB, both `100% GPU` at context 8192.
- **Browser walkthrough** (Edge via `playwright-core` 1.65.1 in the scratchpad, throwaway stack on 18000/18001/15173 with scratch `DATA_DIR`): 29 checks passed in 7 scenarios. The table is in CLAUDE.md §17 Phase 8.
- **Early 413:** `curl` with a 40 MB file against the 35 MB limit got 413 in 3 ms after sending 0.7 MB, with `access-control-allow-origin`.
- **Checks:** `.\check.ps1 -Live`: 12/12 PASS. AI 484 (+13 live), backend 202, frontend 63.

### Known limitations (not changed)
- **Uncited sentences in an emergency answer.** In the chest-pain answer the Verifier passed a draft ending in two uncited sentences of general advice. It touches §7.1 and medical output. **Decision 2026-11-22: left as is for v1**; recorded in CLAUDE.md §17.
- **Classifier routing.** Carried from Phase 7: `medquad-05` and `medquad-10` were still routed to `out_of_scope`.

### Closing (2026-11-22)
- Merged with `--no-ff`; the feature branch is deleted (it was never pushed), there are no stashes and the working tree is clean. `main` was pushed to `origin`.
- Final checks: `.\check.ps1 -Live` 12/12 PASS. AI 484 tests (+13 live), backend 202, frontend 63.
- CLAUDE.md updated: §5, §6.7, §9 (error causes, upload limits endpoint, health `knowledge_base`), §12 (`check.ps1`, report command, `ollama ps` note), §17 (gate, live results, performance, the known limitation).
- Kept, git-ignored, in `data/eval/`: the Phase 7 run (`*-20261122-0845.*`) and the 8.4 performance run (`*-20261122-1111.*`). Both are the inputs `docs/report/README.md` names.
- Removed: `frontend/dist`, pytest/ruff caches and `__pycache__` folders. Also removed from the scratchpad: the throwaway stack's data and logs, the `playwright-core` install, and the 30/40 MB test files. All services are stopped.
- With Phase 8 merged, every v1 milestone in §15 is done.