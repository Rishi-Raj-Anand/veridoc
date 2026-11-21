# Phase 3 — Single-pass pipeline (Milestone 4)

| | |
|---|---|
| **Status** | ✅ **Complete — gate passed 2026-11-21** (on `feature/phase-3-single-pass`, not yet merged) |
| **Branch** | `feature/phase-3-single-pass` |
| **Tasks** | 3.0 – 3.6 |
| **Gate** | A grounded, cited answer end to end over HTTP (`POST /api/chat/message`), no frontend |
| **Touches** | `ai/` and `backend/`. No frontend, no Verifier, no history or transcripts, no safety_check |
| **Record** | At task 3.0, copied to `.claude/plans/phase_3_single_pass_pipeline.md` in the repo. Results are filled in as tasks land (same convention as Phases 0–2) |

---

## Context

Phase 2 left an indexed knowledge base: child chunks in Qdrant, parents in the docstore, and
`OllamaClient.chat()` built but unused. Nothing can answer a question yet. Phase 3 builds the
narrowest end-to-end path:

user message → backend (auth + session ownership) → AI `/internal/chat` → LangGraph
`retrieve → generate → finalize` → `ChatResponse` with `[n]` citations.

Phase 4 then adds the Verifier and escalation on top of this graph, and Phase 5 adds history, transcripts and safety.

**Reused, not rebuilt:**
- `ai_service/llm/ollama_client.py`: `OllamaClient.embed()` / `.chat(messages, temperature=, format=)` (already sets `num_ctx`, `think=False`, `keep_alive`, retry, and `OllamaError`).
- `ai_service/retrieval/vector_store.py`: `VectorStore`, `COLLECTION`. Only a search method is missing; the live Phase 2 checks used `query_points`.
- `ai_service/retrieval/docstore.py`: `Docstore.get_parents` keeps the request order. Not needed until 4.4.
- `ai_service/deps.py`: the `_from_state` pattern. `main.lifespan` builds the objects on `app.state`.
- `ai_service/routers/ingest.py`: the router pattern (`/internal` prefix, `require_internal_key`, `asyncio.to_thread`, `OllamaError` → 503).
- `backend/app/services/ai_client.py`: `_call()` with friendly `AiServiceError` mapping, following the `ingest_document` pattern.
- `backend/app/routers/admin.py`: the `async def` route with DB work in `run_in_threadpool`.
- `backend/app/models/chat_session.py`: the `chat_sessions` table already exists in the initial migration, so **no migration is needed**.
- `langgraph==1.2.24` is already pinned and installed, so there are **no new dependencies**.

## Decisions

| Question | Decision |
|---|---|
| Ungrounded output before the Verifier exists | **Code-level guards now (user choice).** Return the exact §7.4 fallback with `status="fallback"` when: (a) no hit reaches `MIN_RETRIEVAL_SCORE` (no LLM call; this also covers an empty knowledge base), (b) the Generator replies with the sentinel `NOT_IN_DOCUMENTS`, or (c) the draft has no valid `[n]` citation. Graph: `retrieve →(gate) generate →(guard) finalize \| fallback`. Phase 4 adds verify and escalate between these nodes. |
| `status` in Phase 3 | A guarded, cited answer returns `"verified"`. The §9 contract is frozen, and nothing is user-facing before Phase 6, by which point the Verifier is wired in. This is recorded as an interim note in §17. |
| Citation numbering | One `[n]` per **unique `(filename, page)`**. Chunks from the same page are grouped under one number, so the three `disease.md` page-1 chunks become `[1]`, not `[1][2][3]`. Numbers are assigned in best-score order. `citations` lists only the `n` the answer actually uses. Out-of-range markers are removed from the text and logged. Both `[1][2]` and `[1, 2]` are parsed. |
| Top-K config | New `Settings.top_k_precision = 3` (`TOP_K_PRECISION`). §3 says Top-K values live in config. Phase 4 adds the level-2 and level-3 values. |
| Disclaimer and fallback text | Module constants `DISCLAIMER` and `FALLBACK_MESSAGE` in `ai_service/config.py`, the one place, holding the §7.4 and §7.5 text verbatim. Task 5.4 revisits this. `emergency` is always `false` until 5.3. |
| Qdrant thread safety | `VectorStore` gets a `threading.Lock` around every client call. Chat searches run in worker threads and can overlap an ingest. This is finer-grained than sharing the ingest lock, so a chat is not blocked for a whole embed. |
| Chat concurrency | `app.state.chat_lock` (an `asyncio.Lock`) serialises chat turns. With one model in VRAM, running turns in parallel would only thrash it (§6.8). |
| Graph execution | Sync nodes. The compiled graph is built once in `lifespan` (`app.state.pipeline`) and run with `asyncio.to_thread(pipeline.invoke, ...)`. Phase 7's eval script builds the same graph through the same factory. |
| Who may chat | Any authenticated user, the admin included, which helps testing. A session that belongs to another user returns **403**. |
| Message limits | 1–2000 characters after stripping whitespace, validated in both backend and AI service (§6.4 budget). Invalid input returns 422. |
| Timeouts | Backend `CHAT_TIMEOUT_SECONDS = 300`, well above the §6.8 budget of 30–90 s. |

---

## Task 3.0 — Housekeeping
Create branch `feature/phase-3-single-pass` and copy this plan into the repo.

## 3.1 `ai/ai_service/prompts/generator.md` + loader
- `prompts/__init__.py`: `load_prompt(name) -> str` reads `Path(__file__).parent / f"{name}.md"` (UTF-8). A missing file raises at startup.
- Prompt rules:
  - Answer **only** from the numbered sources.
  - Every sentence that states a medical fact ends with `[n]`. Never invent a number.
  - Phrase as "the references suggest…", never as a diagnosis (§7.6).
  - Doses, pediatric use, pregnancy/breastfeeding and interactions only when a source states them explicitly, quoted with the citation. Otherwise, say the documents don't cover it and advise a doctor or pharmacist (§7.3).
  - If the sources do not answer the question at all, reply with exactly `NOT_IN_DOCUMENTS`.
  - Partial gaps are stated in prose.
  - Concise Markdown, no preamble.

## 3.2 `ai_service/retrieval/` — search + `retriever.py`
- `VectorStore.search(vector, limit) -> list[SearchHit]` uses `query_points(COLLECTION, query=..., limit=..., with_payload=True)`. `SearchHit` is a frozen dataclass with `score, doc_id, filename, page, parent_id, chunk_index, text`. The client lock is added here (see Decisions).
- `retriever.py`:
  - `Source(n, filename, page, text, score)`: grouped text, best score.
  - `RetrievalResult(sources, best_score, passed_gate)`.
  - `Retriever(store, llm, settings).retrieve(query) -> RetrievalResult` is iteration 1 only. It embeds the query with `llm.embed([query])[0]`, takes the top `top_k_precision` hits, gates on `best_score >= min_retrieval_score`, and groups hits by `(filename, page)` in best-score order to assign `n`.
  - `format_context(sources) -> str` renders `[n] filename, p. X` followed by the text, per source.
- Kept free of LangGraph so 4.4 can extend it with `iteration`.

## 3.3 `ai_service/agents/generator.py`
- `Generator(llm, settings)` loads `generator.md` once.
- `generate(question, sources) -> str` sends a system prompt plus one user message (`Sources:` block, then `Question:`) with `temperature=0.2`.
- The output is cleaned: any `<think>…</think>` is stripped (Qwen3 can emit an empty one even with `think=False`) and whitespace is trimmed.
- `ai_service/agents/citations.py` holds the pure functions:
  - `extract_citation_numbers(text) -> list[int]`.
  - `clean_citations(text, valid: set[int]) -> (text, used)` removes out-of-range markers.
  - `is_not_covered(text)` checks for the sentinel.

## 3.4 `ai_service/graph/state.py` + `pipeline.py`
- `state.py`: `PipelineState(TypedDict, total=False)` with input `session_id, user_id, message`; working fields `query, iteration, sources, best_score, draft`; output `answer, citations, status, emergency`. Phase 4 adds verifier fields.
- `pipeline.py`: `build_pipeline(retriever, generator) -> CompiledStateGraph`:
  - `START → retrieve`. `retrieve` sets `query = message` (the condenser comes in 5.2) and `iteration = 1`.
  - `retrieve →` conditional edge: `generate` if `passed_gate`, else `fallback`.
  - `generate →` conditional edge: `fallback` if the draft is the sentinel or has no valid citation, else `finalize`.
  - `finalize` cleans the citations, builds `citations=[{n, filename, page}]` for the used numbers only, and sets `status="verified"`.
  - `fallback` sets `answer=FALLBACK_MESSAGE`, `citations=[]`, `status="fallback"`.
  - `finalize` and `fallback` both go to `END`.
  - Every turn is logged with route, best score, latency and citation count. Message content is logged only at DEBUG.
- `run_turn(pipeline, *, session_id, user_id, message) -> PipelineResult` is a thin helper used by the router, and by eval later.

## 3.5 Chat endpoints
**AI service** — `ai_service/routers/chat.py`, `POST /internal/chat`:
- Pydantic models: `ChatRequest{session_id: UUID, user_id: int, message: str 1..2000}`, `Citation{n, filename, page}`, `ChatResponse{answer, citations, status: Literal["verified","fallback"], emergency, disclaimer}`. These match §9 exactly.
- The turn runs `async with chat_lock` and then `await asyncio.to_thread(run_turn, ...)`.
- Errors: `OllamaError` → 503 with its message; anything else → logged and 500.
- Wiring:
  - `deps.py` gains `get_pipeline` / `PipelineDep`.
  - `main.lifespan` builds `Retriever`, `Generator` and the pipeline (prompt load failure stops startup).
  - `create_app` includes the router and creates `chat_lock`.

**Backend:**
- `app/schemas/chat.py`: `ChatMessageRequest{session_id: UUID, message: str}` (stripped, 1..2000), and `Citation` / `ChatResponse` mirroring §9.
- `app/services/chat_service.py`: `claim_session(db, session_id, user) -> None`.
  - Session not found → insert `(id, user_id)` and commit. A concurrent first insert that raises `IntegrityError` → roll back and re-read.
  - Session owned by another user → raise `SessionForbidden`. The service stays HTTP-free, like `document_service`.
- `app/services/ai_client.py`: `CHAT_TIMEOUT_SECONDS = 300`. `send_chat(settings, session_id, user_id, message) -> ChatResponse` uses `_call` and validates the body; an unexpected body raises `AiServiceError`.
- `app/routers/chat.py` (`prefix="/chat"`): `POST /message` is `async def` and takes `CurrentUser`.
  - `claim_session` runs in `run_in_threadpool`; `SessionForbidden` → 403.
  - Then `send_chat`. `AiServiceError` → 503 with "Doctor AI is unavailable right now. Please try again in a moment." The real cause is logged.
  - `main.create_app` includes the router.

## 3.6 Tests + end-to-end smoke
| File | Covers |
|---|---|
| `ai/tests/helpers.py` | `FakeLlm`: scripted `embed` (query → chosen vector) and `chat` (records messages and temperature, returns a scripted reply). `build_app` also sets `pipeline` and `chat_lock` |
| `ai/tests/test_vector_store.py` (+) | `search` ranks by cosine and returns payload fields; respects `limit` |
| `ai/tests/test_retriever.py` | top-3 only; gate below and at the threshold; same-page grouping gives one `n`; `n` follows best-score order; empty collection fails the gate |
| `ai/tests/test_citations.py` | `[1][2]`, `[1, 2]`, out-of-range removal, sentinel detection, no citations |
| `ai/tests/test_generator.py` | system prompt is `generator.md`; `temperature=0.2`; context format carries `[n] filename, p. X`; `<think>` stripped |
| `ai/tests/test_pipeline.py` | happy path → `verified` with only the used citations; low score → exact fallback and **zero `chat` calls**; sentinel → fallback; uncited draft → fallback; `iteration == 1` |
| `ai/tests/test_chat_router.py` | no key → 401; empty or too-long message → 422; 200 body matches §9 incl. `disclaimer`; `OllamaError` → 503 |
| `ai/tests/test_prompts.py` | every `prompts/*.md` loads and is non-empty; `generator.md` contains the sentinel |
| `ai/tests/test_ollama_live.py` (+) | `@pytest.mark.ollama`: `qwen3:8b` chat returns text with `think=False` |
| `backend/tests/test_chat.py` | anonymous → 401; first message creates the `chat_sessions` row; same user continues; another user's session → 403 and the AI is never called; bad UUID or empty message → 422; AI down → 503 with the friendly text; the response passes through unchanged (`ai_client.send_chat` monkeypatched) |
| `backend/tests/test_ai_client_chat.py` | `send_chat` maps unreachable / 5xx / 401 / malformed body to `AiServiceError` (httpx `MockTransport`, following `test_ai_client_documents.py`) |

## `CLAUDE.md` edits (same change, §16)
- **§9:**
  - `/api/chat/message` errors: 401, 403 (another user's session), 422, 503.
  - Message limit of 2000 characters.
  - Any authenticated role may chat.
- **§11:** `TOP_K_PRECISION=3`, also added to `ai/.env.example`.
- **§17 Phase 3:**
  - Tick 3.1–3.6 and record the gate result.
  - Interim note: in Phase 3, `verified` means "passed the code guards"; the Verifier arrives in 4.x.
- **Settled decisions:**
  - Citations are one number per `(filename, page)`.
  - The `NOT_IN_DOCUMENTS` sentinel and the no-citation rule both lead to fallback.
  - `VectorStore` is guarded by a thread lock, and chat turns are serialised.

## Verification
Automated, in `ai/` and `backend/`:
```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
uv run pytest -m ollama      # ai/ only, Ollama running
```
Manual gate. Use spare ports 18000/18001 and a throwaway `DATA_DIR`, as in Phase 2, so the developer's running services and `data/` are untouched:
1. Upload `documents/disease.md` as admin and wait for `ready`.
2. Register and log in a user. Call `POST /api/chat/message` with a new UUID and the message "How is malaria prevented?". Expect a Markdown answer with `[n]` markers, every `n` present in `citations` (`disease.md`, page 1), `status="verified"`, and the disclaimer present.
3. "How is tuberculosis treated?" cites the TB row. Read the answer against the source table to check that it adds nothing beyond it.
4. An off-corpus question ("What is the capital of France?" or "How do I fix my car's brakes?") returns the exact fallback text, `citations=[]`, and `status="fallback"`.
5. A second user reusing the first user's `session_id` gets 403. Stopping the AI service gives 503 with the friendly text.
6. AI log shows per-turn route, best score and latency. `ollama ps` shows `qwen3:8b` at 100% GPU. Record the latency of the first (cold) turn and a warm turn for 4.8.
7. Optional: a second real document, if supplied, to check citation numbering across files.

## Risks
- **Sentinel drift:** the 8B model may paraphrase "not covered" instead of emitting `NOT_IN_DOCUMENTS`. That reply usually has no citations, so guard (c) still catches it. Check this at the gate.
- **Over-strict no-citation guard:** a correct but uncited answer becomes a fallback. That is acceptable (a safe failure); tune the prompt if the gate shows it often.
- **Score gate on short queries:** "what is the CGPA" scored 0.276 in Phase 2, so short medical questions may fall back. Calibration belongs to 4.8; the gate results are noted for it.
- **LangGraph 1.x API:** `StateGraph(PipelineState)`, `add_conditional_edges`, and `compile()` are used; check the installed version's signatures before coding.

## Result (2026-11-21)

**Tasks 3.1 – 3.6 built.** AI service 126 tests (+2 live behind `-m ollama`), backend 148 tests,
`ruff check` and `ruff format --check` clean in both.

**Live smoke** (both services on spare ports 18000/18001, throwaway `DATA_DIR`, real `qwen3:8b` + `bge-m3`,
`disease.md` → 8 chunks):

| Question | Best score | Outcome | Latency |
|---|---|---|---|
| How is malaria prevented? | 0.546 | `verified`, `[1]` = disease.md p. 1, matches the Prevention cell | 9.1 s |
| How is tuberculosis treated? | 0.664 | `verified`, 6-month isoniazid/rifampicin regimen from the row | 13.3 s |
| Treatment for the common cold? | 0.600 | `verified`, rest/fluids/OTC, "no specific cure", 7–10 days | 16.5 s |
| What is the capital of France? | 0.318 | **gate FAIL → exact fallback, no LLM call** | 5.5 s |
| How do I fix my car's brakes? | 0.409 | gate pass, Generator said `NOT_IN_DOCUMENTS` → exact fallback | 10.4 s |

Another user reusing a session → 403; AI service stopped → 503 "Doctor AI is unavailable right now…".
`ollama ps`: `qwen3:8b` 6.2 GB, 100% GPU, 8192 context.

**For 4.8:** the threshold is clearly doing work: the off-corpus France question sat 0.03 under the gate,
and the brakes question (0.409) shows an off-topic query can still pass it, so the sentinel guard
and the Verifier both matter. Latency was 5–16 s per turn here (cold swap included), well inside the
§6.8 budget for a single iteration.

### Deviations from the plan
| Planned | Actual | Why |
|---|---|---|
| smoke users `@smoke.test` | `@example.com` | `email-validator` rejects special-use domains (documented in §3); a smoke-script slip, not a code issue |

### Next
Phase 4 (Verifier & escalation). The running developer AI service on `:8001` predates this phase and needs a
restart (no `--reload`) to expose `/internal/chat`.
