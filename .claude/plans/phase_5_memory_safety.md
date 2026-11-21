# Phase 5 — Session memory & safety (Milestone 6)

| | |
|---|---|
| **Status** | ✅ **Complete — gate passed and merged to `main` 2026-11-21** (merge `3f4d305`) |
| **Branch** | `feature/phase-5-memory-safety` (merged, then deleted) |
| **Tasks** | 5.0 – 5.7 (5.0 housekeeping, 5.7 live gate) |
| **Gate (new, added to §17)** | A two-turn conversation over HTTP: the follow-up is condensed into a standalone question and answered `verified`. A red-flag message comes back with `emergency=true` and the §7.2 notice prefixed. The transcript on disk holds both exchanges with scores |
| **Touches** | `ai/` only, plus `CLAUDE.md`. Backend and frontend unchanged: the §9 `ChatResponse` body is unchanged |
| **Record** | Copied to `.claude/plans/phase_5_memory_safety.md` at 5.0, with results filled in at the end (same convention as Phases 0–4) |

## Context

After Phase 4 every turn is stateless, and `emergency` is always `false`. Phase 5 adds the front of the §6.2 graph, `safety_check → condense_query`, and adds per-session memory:
- Follow-ups such as "what about for children?" are rewritten into standalone questions before retrieval.
- Each exchange is stored in `data/chats/<session_id>.json` for memory and for evaluation.
- Red-flag messages get the §7.2 emergency notice prefixed.

**Reused, not rebuilt:**
- `trim_history(messages, max_tokens, max_messages)` in `agents/budget.py`, which is tested but not wired in yet.
- `retrieve` already reads `state.get("query") or message`, so the condenser only has to set `query`.
- `PipelineResult.attempts / iterations / citations` hold everything the §6.7 transcript needs.
- `FALLBACK_MESSAGE` and `DISCLAIMER` are already defined only in `ai_service/config.py`.
- `load_prompt`, `strip_thinking` and `OllamaClient.chat`. `settings.chats_dir` is already created at startup.
- `FakeLlm` and `build_app` in `ai/tests/helpers.py`, extended.
- `pyyaml` is already a dependency, so there are **no new dependencies**.

## Decisions

| Question | Decision |
|---|---|
| Who sees history | **The condenser only (user choice).** The Generator and the Verifier keep seeing the standalone question plus sources, exactly as now. The §6.4 history budget (6 messages / 1,500 tokens via `trim_history`) applies to the condense call. |
| Emergency scope | **Raw message OR standalone question (user choice).** `safety_check` scans the message first. After condensing, the standalone question is scanned too and the two results are OR-ed. This catches "what should I do now?" after "I have chest pain". It is safety-first, so false positives are accepted. Negations such as "no chest pain" still match; that is deliberate. |
| Where the notice goes | The `finalize` and `fallback` nodes both prefix `EMERGENCY_NOTICE + "\n\n"` when `emergency` is set. A fallback answer gets the notice too, because it is the one case where the user gets nothing else. The `emergency` flag still drives the red banner in Phase 6. |
| First message | No history means no condenser call, so `query = message`. That saves one LLM call and one model swap (§6.8). |
| Bad condenser output | Empty output, over 2,000 characters, or `NOT_IN_DOCUMENTS`-like noise → fall back to the raw message and log a warning. An `OllamaError` propagates to a 503, as elsewhere. |
| Transcript I/O placement | `run_turn` loads history before the graph and saves the exchange after it. Graph nodes stay pure. The Phase 7 eval can pass `store=None` so it does not write files. §6.2's "finalize/fallback save the turn" is reworded to match. |
| Transcript shape (§6.7) | Two entries per exchange. The `user` entry has `content` and `timestamp`. The `assistant` entry has `content` (exactly what the user saw, notice included), `standalone_query`, `status`, `emergency`, `iterations`, `citations`, `verifier_scores` (the `attempts` list) and `timestamp`. Validated with Pydantic models on load and save. |
| History content | The last `HISTORY_MAX_MESSAGES` entries, user and assistant, as `{role, content}`. The emergency notice is stripped from assistant content. Fallback answers stay in: they are short and show the condenser that the previous turn went unanswered. |
| Ownership | The backend already enforces session ownership. As defence in depth, the AI service raises `SessionOwnershipError` when a transcript's `user_id` differs from the request, and the router maps it to **403**. |
| Corrupt transcript | Invalid JSON or schema → rename the file to `<id>.json.corrupt-<ts>`, log a warning, and start the session fresh. A chat turn never fails because of an old file. |
| Save failure | Log the exception and still return the verified answer, so the user is not penalised for a disk error. The next turn simply has less memory. |

---

## 5.0 Housekeeping
- Create `feature/phase-5-memory-safety` from `main`.
- Copy this plan to `.claude/plans/phase_5_memory_safety.md`.

## 5.1 `memory/chat_store.py`
- Pydantic models:
  - `TurnEntry`: `role: Literal["user","assistant"]`, `content`, `timestamp`. Assistant-only fields are optional: `standalone_query`, `status`, `emergency`, `iterations`, `citations`, `verifier_scores`.
  - `Transcript`: `session_id`, `user_id`, `created_at`, `turns`.
- `ChatStore(chats_dir)` has these methods:
  - `load(session_id, user_id) -> Transcript | None` raises `SessionOwnershipError` on a mismatch and quarantines a corrupt file.
  - `history(transcript, max_messages) -> list[dict[str, str]]` returns plain `{role, content}`, with the notice stripped.
  - `append_exchange(session_id, user_id, user_entry, assistant_entry)` loads (or creates) the transcript, appends both entries, and saves.
- `session_id` is parsed as a `UUID` before use as a filename, so there is no path traversal even from the eval script.
- Atomic write:
  1. Write `<id>.json.tmp` in the same directory and fsync it.
  2. `os.replace` it onto `<id>.json`, retrying up to 3× on `PermissionError`, since Windows antivirus or indexing can briefly hold the file.
  3. Write UTF-8 with `ensure_ascii=False`, so ⚠️ round-trips.
- Turns are already serialised by `chat_lock`, so there is no cross-request race.

## 5.2 Condenser: `agents/condenser.py` + `prompts/condense.md`
- `Condenser(llm, settings).condense(history, message) -> str`. `history` is the transcript history after `trim_history(history, settings.history_max_tokens, settings.history_max_messages)`.
  - Empty history → return `message` unchanged, with no LLM call.
  - Otherwise make one chat call at `temperature=0`: the system prompt, then one user message holding the conversation transcript and the latest message.
  - Clean the output: `strip_thinking`, strip surrounding quotes and any `Standalone question:` label, then collapse whitespace.
- What `condense.md` tells the model:
  - Rewrite the latest message as one standalone question, resolving pronouns and references ("that", "for children") from the conversation.
  - Do **not** answer it, add medical facts, or change its intent.
  - If the message is already standalone, return it unchanged.
  - Keep the user's language and output only the question.

## 5.3 Safety: `agents/safety_terms.yaml` + `agents/safety.py`
- The YAML is a list of categories, each with `name`, `patterns` (regexes, matched case-insensitively with word boundaries) and `examples` (phrases that must match, used by tests).
- Categories: chest pain or pressure, difficulty breathing or choking, stroke signs (face droop, slurred speech, one-sided weakness), severe bleeding, suicidal thoughts or self-harm, poisoning or overdose, unconsciousness or unresponsiveness, seizure, severe allergic reaction or anaphylaxis, and severe head injury.
- `SafetyChecker.load(path)` compiles every pattern at startup. A missing file, an invalid regex or an empty category makes startup fail loudly, as `load_prompt` does.
- `check(text) -> list[str]` returns the names of the matched categories.
- Graph nodes, all deterministic with no LLM call:
  - `safety_check` sets `emergency` and `emergency_matches` from `message`.
  - `condense_query` ORs in a check of the standalone `query`.

## 5.4 One place for user-facing text
- Add `EMERGENCY_NOTICE` (exact §7.2 text) to `ai_service/config.py`, next to `FALLBACK_MESSAGE` and `DISCLAIMER`.
- A test asserts:
  - The router's `disclaimer` is `DISCLAIMER`.
  - Fallbacks are exactly `FALLBACK_MESSAGE`.
  - An emergency answer starts with `EMERGENCY_NOTICE`.
  - None of these strings appear as literals anywhere else in `ai_service/` (a grep-style test).

## 5.5 Graph + wiring + logging
- `graph/state.py` gains:
  - `history: list[dict[str, str]]` as an input.
  - `emergency_matches: list[str]` and `condensed: bool` as working fields.
- `graph/pipeline.py` changes:
  - `build_pipeline(retriever, generator, verifier, condenser, safety, settings)`.
  - The graph becomes `START → safety_check → condense_query → retrieve → …`, with the rest unchanged.
  - `finalize` and `fallback` apply the notice through a shared `_with_notice(answer, emergency)` helper.
- `run_turn(pipeline, *, store, session_id, user_id, message)`:
  1. Load the transcript and its history.
  2. Invoke the graph.
  3. Build the two entries and call `append_exchange`, catching and logging any failure.
  4. Return a `PipelineResult` that also has `standalone_query`.
- The per-turn log line adds `emergency=<bool>` with the matched category names, `condensed=<bool>`, `history=<n>` and the existing iterations / scores / latency. Message and standalone text are logged only at DEBUG. Passwords, tokens and JWTs never reach the AI service, so nothing changes there.
- `main.lifespan` builds `Condenser`, `SafetyChecker` and `ChatStore(settings.chats_dir)`. A bad prompt or YAML fails startup.
- `deps.py` gets `ChatStoreDep`. The router passes the store and maps `SessionOwnershipError` → 403.

## 5.6 Tests (mocked LLM)
| File | Covers |
|---|---|
| `tests/helpers.py` | `FakeLlm` gets a `condense_replies` script. A condenser call is recognised by its system prompt (the loaded `condense.md`) and logged as `chat:condense` in `events`. `build_app` wires the condenser, safety checker and store |
| `test_safety.py` | Every YAML `examples` phrase matches its category. Every pattern is hit by at least one example. Matching is case-insensitive. Benign texts ("I have a mild headache", "malaria prevention") do not match. A bad regex or a missing file fails at load |
| `test_condenser.py` | No history → unchanged and no LLM call. "what about for children?" after a migraine turn → the scripted standalone question, at `temperature=0`, with history in the message. History is trimmed to 6 messages / the token budget. Empty or overlong output → raw message. `<think>`, quotes and the label are stripped |
| `test_chat_store.py` | Round-trip of an exchange. The second exchange appends. Atomic write leaves no `.tmp` behind. A failing `os.replace` (monkeypatched) leaves the old file intact. A corrupt file is quarantined and the session starts fresh. A user_id mismatch raises. A non-UUID session id is rejected. ⚠️ survives a round-trip |
| `test_pipeline.py` (+) | The second turn condenses and the retrieval embed uses the standalone query. The Generator and Verifier messages contain the standalone question and no history. An emergency in the message → `emergency=True` and the answer starts with the notice, on both a verified answer and a fallback. An emergency only in the standalone query → flagged. A transcript is written with both entries and the scores. `store=None` writes nothing. A save failure still returns the answer. Event order: `chat:condense` → `embed` → `chat:gen` → `chat:verify`, with no embed between gen and verify |
| `test_chat_router.py` (+) | Two-turn HTTP conversation. Another `user_id` on an existing session → 403. The emergency response body |
| `test_prompts.py` / `test_config.py` (+) | `condense.md` loads. `EMERGENCY_NOTICE` is the exact §7.2 text |
| `test_ollama_live.py` (+) | `@pytest.mark.ollama`: real `qwen3:8b` condenses "what about for children?" into a question that mentions the earlier topic |

## 5.7 Live gate
Run on a throwaway `DATA_DIR` with both services on ports 18000/18001, as in Phases 3–4, so the real `data/` is untouched:
1. Upload `documents/disease.md` and a synthetic migraine document (scratchpad), and wait for `ready`.
2. Turn 1: "What is the treatment for migraine?" → `verified`. Turn 2 on the same session: "what about for children?" → the AI log shows `condensed=true`. The standalone question (DEBUG) names migraine and children, and the answer is `verified` or the exact fallback, never off-topic.
3. Turn 1: "How is malaria treated?". Turn 2: "how can I prevent it?" → `verified`, about malaria prevention.
4. "I have crushing chest pain and can't breathe, what should I take?" → `emergency=true`, and the answer starts with the §7.2 notice whether it is verified or a fallback.
5. Follow-up "what should I do now?" after an emergency turn → flagged through the standalone check.
6. `data/chats/<session>.json` is valid JSON with 4 entries, standalone queries and verifier scores. The backend rejects a second user reusing the session with 403.
7. Record latency for the second turn (condense adds one LLM call and keeps the 1-switch-pair floor) and the largest prompt tokens.

## Commits
1. `docs: add Phase 5 plan`
2. `feat: chat transcript store with atomic writes (5.1)`
3. `feat: deterministic emergency safety check and notice (5.3-5.4)`
4. `feat: query condenser and session memory in the pipeline (5.2, 5.5-5.6)`
5. `docs: record Phase 5 gate result and decisions`

Merge to `main` once the gate passes.

## `CLAUDE.md` edits (same change, §16)
- **§6.2:**
  - `safety_check` scans the message and the standalone question.
  - `finalize`/`fallback` prefix the notice.
  - The turn is saved by `run_turn` after the graph.
- **§6.7:** the two-entries-per-exchange transcript shape, and corrupt-file quarantine.
- **§17:**
  - Add the Phase 5 gate line.
  - Tick 5.1–5.6.
  - Record the gate result.
  - Add these to *Settled decisions*: history goes to the condenser only, the emergency scope, the notice also on fallbacks, the 403 on an ownership mismatch, and that a save failure does not fail the turn.
- **§11:** no new variables (`HISTORY_MAX_MESSAGES` and `HISTORY_MAX_TOKENS` already exist).

## Verification
```powershell
# ai/ (backend untouched, re-run once to confirm)
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
uv run pytest -m ollama        # live condenser + existing live checks, Ollama running
```
Then the 5.7 live gate. Afterwards, restart the real AI service on `:8001`, since it runs without `--reload`.

## Risks
- **Condenser drift:** an 8B model may answer the question instead of rewriting it, or inject facts. The prompt forbids it, cleaning catches the obvious failures, and the Verifier still audits against the sources only, so the worst case is a weaker retrieval query, never an ungrounded answer.
- **Emergency false positives:** keyword matching flags "no chest pain" and topic follow-ups. This is accepted as safety-first, and the notice is advisory text, not a refusal.
- **Extra latency on follow-ups:** one condense call (~1–3 s warm). The LLM is already resident from the previous turn's verify, so no extra swap is added.
- **Windows file locking** on `os.replace` is handled by the short retry. If a save still fails, the turn still answers.

## Result (2026-11-21)

**Tasks 5.1 – 5.6 built.** AI service 340 tests (+5 live behind `-m ollama`, all passing against real Ollama), backend 148 tests, `ruff check` and `ruff format --check` clean in both. No backend or frontend changes; the §9 `ChatResponse` body is unchanged. Commits: `ffb85a4` plan, `ad3e492` transcript store, `4018a96` safety check, `5c9674a` condenser + pipeline wiring, `0a2b6ec` docs + gate result, merged as `3f4d305`.

**Live gate** (real `qwen3:8b` + `bge-m3`, throwaway `DATA_DIR`, AI service on :18001, `disease.md` + a synthetic 3-section migraine document):

| Turn | Result | Latency |
|---|---|---|
| "What is the treatment for migraine?" | `verified`, cites `migraine.md` | 10 s |
| "what about for children?" | standalone = "What is the treatment for migraine in children?", `verified`, answer from the children section | 15 s |
| "How is malaria treated?" then "how can I prevent it?" | standalone = "How can I prevent malaria?", `verified` | 15 s each |
| Chest pain + "can't breathe" | `emergency=true` (`chest_pain`, `breathing`), notice prefixed; `verified` on one run, `fallback` (3× uncited) on another | 16-18 s |
| "what should I do now?" (after it) | **first run: not flagged** (see below); after the prompt fix: `emergency=true`, `fallback` | 17 s / 32 s |
| Same session, a different `user_id` | 403, no LLM call | instant |

Transcripts on disk held every exchange with `standalone_query`, `emergency`, `iterations` and per-attempt scores. A follow-up turn costs one extra condense call and no extra model swap (11-16 s warm).

### Live findings
| Finding | Action |
|---|---|
| **The condenser left "what should I do now?" unchanged** (it judged it already standalone), so the standalone scan could not see the earlier chest pain, and the answer was off-topic (migraine and cold advice) | Fixed in `prompts/condense.md`: a message that names no subject inherits the conversation's subject, with this exact case as the example. Re-probed live: "what should I do now?" → "What should I do for crushing chest pain and difficulty breathing?"; topic switches ("How is malaria prevented?" after migraine) are left alone. A prompt test pins the rule |
| **The Verifier passed that off-topic answer at faithfulness 1.00 / relevance 1.00.** Not a Phase 5 regression: with no subject, retrieval returned unrelated chunks, and the 8B judge did not penalise an answer that ignores the question | **Open.** The condenser fix removes this trigger, but the Verifier's leniency on vague questions is a weakness to look at in Phase 7 (RAGAS) or 8 |
| One chest-pain run was `verified` with an answer that mixed a cited asthma line with an uncited "could indicate a heart attack" sentence, both passing the audit | **Open.** Same Verifier-strictness question; the notice was present either way |

### Carry-forward notes
| For | Note |
|---|---|
| Phase 6 | `emergency` and the notice text are in the response; the banner should key off `emergency`, and may avoid repeating the notice text if it is already the first line of `answer` |
| Phase 7 | `run_turn(..., store=None)` gives the stateless pipeline run the eval needs |
| running services | Done at close-out: the AI service on `:8001` was restarted on the Phase 5 code |

## Close-out (2026-11-21)

**Final state of `main`:** merge `3f4d305` (feature commits `ffb85a4`, `ad3e492`, `4018a96`, `5c9674a`, `0a2b6ec`). AI service 340 tests (+5 live behind `-m ollama`, passing against real Ollama), backend 148 tests, `ruff check` and `ruff format --check` clean in both, re-run on `main` after the merge. Only `main` remains: the feature branch was deleted, there are no stashes and no remotes.

**Completeness check:** every file named in tasks 5.1-5.6 exists, §17 shows 5.1-5.6 ticked with the gate line, no new environment variables were needed (`HISTORY_MAX_MESSAGES` and `HISTORY_MAX_TOKENS` already existed, so both `.env.example` files are unchanged), and no TODO/FIXME markers were left in `ai_service/`.

**Servers:** the AI service on `:8001` (no `--reload`) was restarted on the Phase 5 code from the Claude Code session. `/internal/health` is `ok` (Ollama, both models, Qdrant on the real `data/vectors`), it returns 401 without the key, and the backend on `:8000` reports `ok` through it. As in Phase 4, it was started from the Claude Code session, not from the developer's own terminal.

**Clean-up performed:** the gate driver, probe scripts, throwaway `DATA_DIR` and logs were removed from the scratch area; ports 18000/18001 are free; the developer's real `data/` (`chats/` still empty) and `documents/` were not touched; the plan-mode draft was removed from `~/.claude/plans/`.

**Not done here:** a live chat against the real `data/` (no documents are indexed there yet, so a question would fall back until one is uploaded). The two open Verifier-strictness findings above stay open for Phase 7/8.
