# Phase 4 — Verifier & escalation (Milestone 5)

| | |
|---|---|
| **Status** | ✅ **Complete — gate passed and merged to `main` 2026-11-21** (merge `9f1e5e0`) |
| **Branch** | `feature/phase-4-verifier` (merged, then deleted) |
| **Tasks** | 4.0 – 4.8 |
| **Gate** | A question answerable only from a parent chunk reaches it by iteration 2; an off-corpus question returns the fallback and never a guess |
| **Touches** | `ai/` only, plus `CLAUDE.md`. No backend or frontend changes: the §9 `ChatResponse` contract is unchanged |
| **Record** | Copied to `.claude/plans/phase_4_verifier_escalation.md` at 4.0; results filled in below (same convention as Phases 0–3) |

## Context

Phase 3 shipped `retrieve → generate → finalize | fallback`. In that phase, `status="verified"` only means the draft passed three code guards. Phase 4 turns it into the real dual-agent loop from §6.2:

- A **Verifier** audits every draft against the retrieved context.
- A failed draft **escalates** retrieval: Top-8 children → ≤4 parents, then Top-15 → ≤6 parents.
- The loop gives up after **3 iterations** with the exact §7.4 fallback.

After this phase, `verified` means what it says, and the Phase 3 interim note in §17 is removed.

**Reused, not rebuilt:**
- `OllamaClient.chat(messages, temperature=, format=)` in `ai_service/llm/ollama_client.py` already takes a JSON schema `format`.
- `Docstore.get_parents(ids)` in `ai_service/retrieval/docstore.py` keeps the request order, so it is ready for "ordered by best child score".
- From `retrieval/retriever.py`: `group_sources`, `format_context`, `Source`, `RetrievalResult`. `VectorStore.search(vector, limit)`.
- From `agents/citations.py`: `clean_citations`, `is_not_covered`, `strip_thinking`. `load_prompt` in `prompts/__init__.py`.
- The `PipelineState` fields `iteration`, `sources`, `best_score` and `gate_passed`, and `run_turn` / `PipelineResult` in `graph/pipeline.py`.
- The `len(text) / 4` token estimator in `ingestion/chunker.py`, reused by the budget trimmer.
- `FakeLlm` and `build_app` in `ai/tests/helpers.py`, extended rather than replaced.
- There are **no new dependencies**.

## Decisions

| Question | Decision |
|---|---|
| Retrieval-score gate | **Literal §6.3 (user choice).** Iteration 1 below `MIN_RETRIEVAL_SCORE` skips straight to iteration 2 with no LLM call. Iteration 2 **generates regardless of score** and the Verifier judges. The final iteration below the gate goes to `fallback` with no LLM call. An off-corpus question therefore costs about one generate round before it falls back. |
| `NOT_IN_DOCUMENTS` / uncited draft | Both count as a **failed iteration without calling the Verifier**: escalate if `iteration < MAX_ITERATIONS`, otherwise fallback. Escalation is what can surface the parent that holds the answer, so these must not short-circuit to fallback any more. |
| Escalation query | Retrieval query = standalone question + the **latest** `missing_info`, not every earlier one appended. Accumulating text would dilute the embedding. The Generator and Verifier always see the original question. |
| Re-embedding | The query vector is kept in state and reused when the retrieval query is unchanged, which covers a gate skip, the sentinel, and a `missing_info` of null. Re-embedding the same text after an LLM call would cost a ~7 s `bge-m3` swap for nothing (§6.8). |
| Parent sources | One `Source` per `(filename, page_start)`, keeping the settled one-number-per-place rule. The citation page is `page_start`, the context label reads `p. 3–4` when a parent spans pages, and the score is the parent's best child score. If a parent is missing from the docstore, its child texts are used instead and a warning is logged. |
| Context identity | Generator and Verifier receive **the same trimmed source list**. The draft passed to the Verifier is the citation-cleaned text, the exact text `finalize` would return. |
| Verifier failure modes | Invalid JSON or schema (including scores outside 0–1) → one retry → a `fail` with `parse_failed=True`. `OllamaError` is **not** a fail: it propagates to a 503, as it does today. |
| Iteration cap | `MAX_ITERATIONS` is validated to `1..3` in `Settings`, because §6.3 defines only three levels. A misconfigured cap stops startup instead of silently running a fourth pass. |
| New config (§3: Top-K values live in config) | `TOP_K_LEVEL2=8`, `MAX_PARENTS_LEVEL2=4`, `TOP_K_LEVEL3=15`, `MAX_PARENTS_LEVEL3=6`, `CONTEXT_MAX_TOKENS=4800`, `HISTORY_MAX_TOKENS=1500` (§6.4) |

---

## 4.0 Housekeeping
Create the branch and copy this plan into `.claude/plans/`.

## 4.1 `prompts/verifier.md` + schema
- `ai_service/agents/verifier.py` defines `VerifierOutput(BaseModel)`, which is the §6.5 schema exactly:
  - `faithfulness: float` (0–1) and `relevance: float` (0–1).
  - `unsupported_claims: list[str]`.
  - `missing_info: str | None`.
  - `verdict: Literal["pass","fail"]`.
  - The schema sent as `format=` is `VerifierOutput.model_json_schema()`.
- What `verifier.md` tells the model:
  - It is an auditor, and it uses only the numbered sources.
  - A claim that is true but absent from the sources is **unsupported**, and so is a claim whose `[n]` points at the wrong source.
  - Faithfulness is the share of factual claims supported by their cited source.
  - Relevance is how well the draft answers the question.
  - Doses, pediatric use, pregnancy and interactions must be explicitly stated in a source (§7.3).
  - Hedges and advice such as "consult a doctor" or "the documents do not cover X" are not factual claims.
  - `missing_info` is a short search phrase naming what the sources lack, or null.
  - Output JSON only.

## 4.2 `agents/verifier.py`
- `Verifier(llm, settings).verify(question, sources, draft) -> VerifierResult`, where `VerifierResult` is a frozen dataclass.
  - The call sends the system prompt plus one user message: `Sources:` (same `format_context`), then `Question:`, then `Draft answer:`.
  - It runs at `temperature=0` with `format=schema`. There is no chat history, so the audit uses the retrieved context only.
  - The reply goes through `strip_thinking` and then `VerifierOutput.model_validate_json`. On failure there is one retry. A second failure returns `VerifierResult.parse_failure()`.
  - Normalisation: blank `unsupported_claims` entries are dropped, and an empty or whitespace `missing_info` becomes `None`.

## 4.3 Pass rule in code
- `passes(output, settings) -> bool` is a pure function:
  `verdict == "pass" and faithfulness >= FAITHFULNESS_THRESHOLD and relevance >= RELEVANCE_THRESHOLD and not unsupported_claims`.
- `VerifierResult.passed` comes only from this function. The model's `verdict` on its own is never trusted.

## 4.4 Escalation in `retrieval/retriever.py`
- Split `Retriever` so embedding and search are separate steps:
  - `embed_query(query) -> list[float]`.
  - `retrieve(vector, iteration) -> RetrievalResult`.
  - `Retriever` now takes the `Docstore`, wired in `main.lifespan` and `tests/helpers.build_app`.
- Level 1 is unchanged: Top-`top_k_precision` children, grouped by `group_sources`.
- Levels 2 and 3 work the same way:
  1. Take the Top-`top_k_levelN` children.
  2. Keep each `parent_id` once, ordered by best child score.
  3. Take the first `max_parents_levelN`.
  4. Fetch them with `docstore.get_parents`.
  5. Build `Source`s through a new `group_parents` helper (numbering by score, as `group_sources` does).
- The gate always uses the best **child** score. `RetrievalResult` gains `mode: Literal["precision","expansion"]`.
- `Source` gains `page_end` (defaulting to `page`) so `format_context` can show page ranges.

## 4.5 Graph: `verify`, `escalate`, `fallback`
`graph/state.py` gains these fields:
- `retrieval_query`, `query_vector` and `vector_query`, which power the vector reuse.
- `draft` (cleaned) and `verification: VerifierResult | None`.
- `attempts: Annotated[list[Attempt], operator.add]`. Each `Attempt` records iteration, mode, best_score, source count, outcome (`low_score|not_covered|uncited|pass|fail`), and faithfulness/relevance when the Verifier ran.

`graph/pipeline.py` becomes `build_pipeline(retriever, generator, verifier, settings)`:

```
START → retrieve
retrieve  ─ gate pass ───────────────────────────────→ generate
          ─ gate fail, final iteration ──────────────→ fallback
          ─ gate fail, iteration 1 ──────────────────→ escalate
          ─ gate fail, otherwise (iter 2) ───────────→ generate
generate  ─ sentinel / empty / no valid [n] ─────────→ escalate | fallback (final)
          ─ otherwise ───────────────────────────────→ verify
verify    ─ passed ──────────────────────────────────→ finalize
          ─ failed ──────────────────────────────────→ escalate | fallback (final)
escalate  (iteration += 1; retrieval_query = query + latest missing_info) → retrieve
finalize, fallback → END
```

- `retrieve` embeds only when `retrieval_query != vector_query`, then applies the 4.6 context trim.
- `generate` and `verify` are adjacent edges, so no embedding call can happen between them (§6.8).
- `PipelineResult` gains `iterations`, `attempts` and `sources` (the final context). Phase 5 transcripts and Phase 7 eval need them. The router ignores them and the §9 body is unchanged.
- `run_turn` logs one line per turn: status, iterations, each attempt's outcome, score and faithfulness/relevance, and latency. Message text is logged only at DEBUG.

## 4.6 Prompt-budget trimmer, `ai_service/agents/budget.py`
- `trim_sources(sources, max_tokens)` drops the **lowest-scoring** sources first. Because `n` follows score order, numbering stays contiguous. It always keeps at least one source, truncating that source's text if it alone is over budget. It is applied in `retrieve`, so the Generator and the Verifier see the same list.
- `trim_history(messages, max_tokens, max_messages)` keeps the newest messages. It is unit-tested now and wired in at 5.2, since there is no history yet.
- The system prompt is never trimmed.
- `OllamaClient.chat` logs Ollama's `prompt_eval_count` / `eval_count`, so the ~7,000-token budget can be confirmed live, at 4.8 and 8.4.

## 4.7 Tests (mocked LLM)
| File | Covers |
|---|---|
| `tests/helpers.py` | `FakeLlm` gets separate scripted queues for generator replies and verifier replies (a verifier call is one with `format` set), plus an `events` log (`embed` / `chat:gen` / `chat:verify`) for ordering checks. `build_app` wires the `Docstore` and `Verifier` |
| `test_verifier.py` | pass-rule truth table: each condition fails on its own, and values exactly at the thresholds pass. Blank claims are ignored. `temperature=0` and the `format` schema are sent. The message has sources, question and draft and no history. Malformed JSON once → the retry succeeds. Malformed twice → `fail`, `parse_failed`, exactly 2 calls. An out-of-range score counts as malformed |
| `test_retriever.py` (+) | level 2 Top-8 → ≤4 parents, de-duplicated and ordered by best child score. Level 3 → ≤6. Page-range label. A missing parent falls back to the children. The gate uses the child score |
| `test_budget.py` | lowest score is dropped first; numbering stays contiguous; one oversized source is truncated, never removed; history keeps the newest messages |
| `test_pipeline.py` (rewritten) | pass at iteration 1 → `verified`, 1 iteration. Fail then pass → the iteration-2 generator message holds **parent** text, and the second embed includes `missing_info`. 3 fails → exact fallback, 3 Verifier calls. Always-fail never goes past 3 iterations or 6 chat calls. Low score at iteration 1 → no LLM call at iteration 1, generate at iteration 2. Low score through iteration 3 → fallback. Sentinel and uncited drafts → escalate with no Verifier call. Malformed JSON every time → fallback. `MAX_ITERATIONS=1` → single attempt. An unchanged query is not re-embedded. The `events` log never has `embed` between `chat:gen` and `chat:verify` |
| `test_config.py` (+) | `MAX_ITERATIONS` 0 or 4 is rejected |
| `test_prompts.py` (+) | `verifier.md` loads |
| `test_chat_router.py` | still green, plus one escalation-to-fallback case over HTTP |
| `test_ollama_live.py` (+) | `@pytest.mark.ollama`: real `qwen3:8b` returns JSON that validates against `VerifierOutput`, including the nullable `missing_info` |

## 4.8 Calibration pass (live)
- Use a throwaway `DATA_DIR` and spare ports, as in Phase 3, so the running services and the real `data/` are untouched. A scratchpad driver runs `build_pipeline` in-process, because that exposes `attempts`, which HTTP hides.
- Documents: `documents/disease.md`, plus any further real reference docs the team supplies. Add a **synthetic gate document** (scratchpad only) where the answer paragraph shares a parent with the paragraph the question matches but sits in a different child. Before trusting the gate result, confirm from `attempts` that the answer child is not in the iteration-1 Top-3.
- About 12 questions: 6 in-corpus, 2 short low-scoring ones, 2 off-corpus (France, car brakes), 1 dosage or pediatric question the source doesn't cover, and the parent-only gate question.
- For each attempt, record best score, faithfulness, relevance, verdict, outcome, prompt tokens and latency.
- Choose `FAITHFULNESS_THRESHOLD`, `RELEVANCE_THRESHOLD` and `MIN_RETRIEVAL_SCORE`, and record the values and the reasoning in §17. Expect a lenient 8B Verifier; if it passes the off-corpus drafts, tighten the prompt before the thresholds.
- Decide whether the worst-case latency (3 iterations) is acceptable or whether the `qwen3:4b` escape hatch is needed. Switching to it means updating §3 and §11, and asking first.

## Commits
1. `docs: add Phase 4 plan`
2. `feat: verifier agent with code-computed pass rule (4.1-4.3)`
3. `feat: parent-chunk escalation levels in the retriever (4.4)`
4. `feat: verify/escalate/fallback loop and prompt budget (4.5-4.7)`
5. `docs: record Phase 4 calibration and gate result`

Merge to `main` once the gate passes.

## `CLAUDE.md` edits (same change, §16)
- **§6.3:** state the clarified gate semantics:
  - Iteration 2 generates regardless of score.
  - Sentinel and uncited drafts count as a failed iteration.
  - The retrieval query is the question plus the latest `missing_info`.
  - The query vector is reused when the query is unchanged.
- **§11** and `ai/.env.example`: add the six new variables, and the calibrated threshold values if they change.
- **§17:**
  - Tick 4.1–4.8, record the gate and calibration results, and delete the Phase 3 "interim" note.
  - Add the decisions above to *Settled decisions*.

## Verification
Automated, in `ai/` (backend untouched, but re-run it once to confirm):
```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
uv run pytest -m ollama      # live structured-output check, Ollama running
```
Live gate, on a throwaway `DATA_DIR` with both services on 18000/18001:
1. Upload `disease.md` and the synthetic gate doc as admin, and wait for `ready`.
2. Parent-only question → `verified`. The AI log shows attempt 1 = `fail` or `not_covered`, then attempt 2 = `pass` in expansion mode, with the citation pointing at the right file and page.
3. "What is the capital of France?" → exact fallback, `citations=[]`. The log shows iteration 1 `low_score` (no LLM), iteration 2 generated and rejected, iteration 3 `low_score` → fallback.
4. "How do I fix my car's brakes?" → exact fallback, never a guess.
5. In-corpus malaria and TB questions still answer `verified`, with the same quality as in Phase 3.
6. `ollama ps` shows `qwen3:8b` at 100% GPU. Logged prompt tokens stay under ~7,000. Record the worst-case 3-iteration latency (it must stay inside the 300 s backend timeout).

## Risks
- **Nullable field in Ollama structured output:** `missing_info: str | None` becomes `anyOf` with null. If Ollama's grammar rejects it, the live test catches it. Fallback: send `missing_info` as a string with `""` meaning none, and normalise it to `None` in code. The §6.5 shape stays unchanged at the Pydantic layer.
- **Lenient or harsh 8B Verifier:** it may pass everything, or flag advice such as "consult a doctor" as unsupported. The 4.8 calibration and prompt wording address this. Fallback is always the safe outcome.
- **Latency:** each iteration adds a Verifier call, and each escalation adds a model swap. The worst case is roughly 60–90 s, which is inside the 300 s backend and Ollama timeouts but must be measured at 4.8.
- **Off-corpus cost:** with the literal §6.3 gate, off-corpus questions now spend one generate round before they fall back. This was accepted when the gate rule was chosen.

## Result (2026-11-21)

**Tasks 4.1 – 4.8 built.** AI service 180 tests (+4 live behind `-m ollama`), backend 148 tests, `ruff check` and `ruff format --check` clean. Commits: `fd3761a` plan, `139be90` code 4.1–4.7, `63961a2` docs + gate result, merged as `9f1e5e0`.

**Live calibration** (in-process on a throwaway `DATA_DIR`, real `qwen3:8b` + `bge-m3`, `disease.md` + a synthetic migraine document):

| Question | Best score | Attempts | Latency |
|---|---|---|---|
| Malaria / TB / common cold / aspirin-dengue / hypertension / typhoid | 0.55 / 0.66 / 0.60 / 0.56 / 0.57 / 0.58 | `pass` at iteration 1 (1.00 / 1.00) | 11–19 s |
| "dengue medicine", "cold cure" (short) | 0.58 / 0.53 | `pass` at iteration 1 | 15–18 s |
| Capital of France | 0.32 | `low_score`, `not_covered`, `low_score` → fallback | 14 s |
| Car brakes | 0.41 | `not_covered` ×3 → fallback | 15 s |
| Paracetamol dose, 5-year-old | 0.51–0.53 | `fail` (faithfulness 0.00), `not_covered` ×2 → fallback | 31 s |
| Migraine in children (parent-only) | 0.65 | `fail` (0.50), `pass` at iteration 2 from the parent | 35 s |

The parent-only gate was first run against a 2-chunk synthetic document and passed at iteration 1, which proved nothing: the answer chunk was inside the Top-3. The document was lengthened and a probe confirmed the answer text is absent from level-1 context and present at level 2 before the run above was accepted.

**Calibration outcome.** Thresholds unchanged (0.85 / 0.75 / 0.35): the Verifier is bimodal (1.00 vs ≤ 0.50). The `qwen3:4b` escape hatch is not needed: one iteration is 11–19 s, two are 30–35 s, and the 3-iteration worst case (~50–60 s) is extrapolated, not measured. Largest prompt: 2,414 tokens.

### Deviations from the plan
| Planned | Actual | Why |
|---|---|---|
| Gate: low score at iteration 2 always generates | A retrieval with **no sources at all** goes straight to fallback | An empty knowledge base would otherwise call the Generator on an empty context |
| Synthetic gate document of 2 chunks | 4+ chunks with the answer in the last paragraph | The 2-chunk version put the answer inside the Top-3, so it never escalated |

### Carry-forward notes
| For | Note |
|---|---|
| 5.2 condenser | `retrieve` sets `query = state.get("query") or message`; the condenser should set `query` before it. `trim_history` exists and is tested but not wired in yet |
| 5.1 transcripts | `PipelineResult.attempts` / `iterations` / `sources` hold what §6.7 wants to store (scores never shown to users) |
| 5.3 safety | `emergency` is still always `false`; the notice is prefixed at the end of the turn, after `finalize` |
| 7.3 ablation | single-pass without the Verifier can be had with `MAX_ITERATIONS=1` plus a verifier stub, or by calling `Generator` directly |
| running services | the AI service on `:8001` runs without `--reload`, so it must be restarted to pick up Phase 4 |

## Close-out (2026-11-21)

**Final state of `main`:** merge `9f1e5e0`, then `7ed20c5` (plan record). AI service 180 tests (+4 live behind `-m ollama`, all passing against real Ollama), backend 148 tests, `ruff check` and `ruff format --check` clean in both, re-run on `main` after the merge. Only `main` remains: the feature branch was deleted, there are no stashes and no remotes.

**Clean-up performed:** the calibration driver, its throwaway `DATA_DIR`, logs, results and the synthetic migraine document were removed from the scratch area; ports 18000/18001 are free; the developer's real `data/` (`chats/` still empty) and `documents/` were not touched.

**Servers:** the AI service on `:8001` (no `--reload`) was restarted on the Phase 4 code after the new variables were added to `ai/.env` (`TOP_K_PRECISION`, `TOP_K_LEVEL2`, `MAX_PARENTS_LEVEL2`, `TOP_K_LEVEL3`, `MAX_PARENTS_LEVEL3`, `CONTEXT_MAX_TOKENS`, `HISTORY_MAX_TOKENS`, each with a comment). `/internal/health` is `ok` (Ollama, both models, Qdrant on the real `data/vectors`), it returns 401 without the key, and the backend on `:8000` reports `ok` through it. Note: the restarted service was started from the Claude Code session, not from the developer's own terminal.

**Not done here:** a live chat against the real `data/`: it has no documents indexed yet, so a question would fall back until one is uploaded.

**Left alone (not from this phase):** an untracked `.vscode/settings.json` (`python.terminal.useEnvFile`) and an uncommitted one-line change to `CLAUDE.md` that replaces a blank line in *Settled decisions* with the text `ya`, which looks accidental. Both were already in the working tree and were excluded from the Phase 4 commits.
