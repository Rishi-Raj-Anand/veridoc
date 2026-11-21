# Phase 6B — Intent routing (small talk / off-topic / medical) + admin "Assistant behaviour"

| | |
|---|---|
| **Status** | ✅ **Complete 2026-11-21 — gate passed, on `feature/6b-intent-routing`, not yet merged** |
| **Branch** | `feature/6b-intent-routing` from `main` (`ecaf2ba`) |
| **Touches** | `ai/`, `backend/`, `frontend/`, `CLAUDE.md`. **No new dependencies.** |
| **Record** | Copy this plan to `.claude/plans/phase_6b_intent_routing.md` at 6B.0, and fill in the results at the end |
| **Order** | Lands **before Phase 7**, because the eval must measure the pipeline that ships |

## Context

Typing "hi" returns *"I couldn't find verified information in my medical references…"*. The cause: every message goes straight into the medical RAG loop (`safety_check → condense_query → retrieve → …`).
- "hi" retrieves nothing over `MIN_RETRIEVAL_SCORE`, or the Generator answers `NOT_IN_DOCUMENTS`.
- The pipeline then correctly refuses to guess and returns the §7.4 fallback.
- Nothing in the graph asks *what kind of message is this?*

You proposed a three-way classifier, which is the right design:

| Intent | Example | Reply |
|---|---|---|
| `smalltalk` | "hi", "how are you?", "thanks!" | A short, friendly LLM reply that contains no medical content |
| `out_of_scope` | "write my essay", "recipe for pasta", "fix my code" | "Sorry, I can't help with that…" (admin-editable text) |
| `medical` | "what helps a migraine?" | The existing dual-agent RAG loop, unchanged |

Four refinements make it safe:
1. **Emergency always wins.** If `safety_check` matched, the intent is forced to `medical`, so an emergency is never small-talked or refused.
2. **When unsure, choose `medical`.** This covers mixed messages ("hi, what is malaria?") and any classifier parse failure. The RAG loop is the safe default, because the worst it can do is fall back.
3. **The classifier sees recent turns**, so a follow-up like "what about for kids?" counts as medical.
4. **Admin edits can only add restrictions.** The §7 safety rules stay locked and always come after the admin's text in the prompt.

You also asked for an admin section to write and update the LLM's behaviour and guardrails. Your choices:
- What the admin writes **changes the prompts**, while the safety rules stay locked.
- Small talk gets a **short LLM reply**.
- The **disclaimer stays on every reply**, so §7.5 is unchanged.

## Decisions

| Question | Decision |
|---|---|
| Graph shape | `safety_check → classify_intent →` then one of:<br>• `medical` → `condense_query → retrieve → …` (unchanged)<br>• `smalltalk` → `smalltalk_reply → END`<br>• `out_of_scope` → `decline → END`<br>Classifying **before** condensing means small talk never pays for a condense call, and the condenser can never turn "thanks!" into a medical question. |
| Greeting fast path | A deterministic whole-message regex: hi / hello / hey / good morning / thanks / thank you / ok / bye, plus punctuation and emoji. It goes to `smalltalk` with **no classifier LLM call**, so "hi" is answered by a single LLM call. |
| Classifier | `agents/classifier.py` returns `IntentOutput {intent}` through `format=<schema>` at temperature 0.<br>• Input: the message, the last 2 history entries (notice stripped), and the admin's `scope` text.<br>• Parse failure: retry once, then `medical`.<br>• `OllamaError`: a 503, as elsewhere.<br>• Emergency match: skipped entirely, intent `medical`. |
| Small-talk reply | `agents/smalltalk.py` with `prompts/smalltalk.md`, temperature 0.3, no history.<br>• The prompt says: be brief (≤ 3 sentences); you are Doctor AI, which answers health questions from its reference documents; **never give medical facts, advice, doses or diagnoses**; if the user raises a health topic, invite them to ask it as a question.<br>• Output is passed through `strip_thinking` and capped at 600 chars.<br>• An empty reply gets a fixed greeting. |
| Decline | Deterministic, no LLM: returns the admin's `out_of_scope_message`. |
| Model residency (§6.8) | The classifier runs **before** retrieval, while `qwen3:8b` is usually still resident from the previous turn, so normally there is no extra swap. The worst case is +1 swap on the first turn after an ingest. Small talk and declines never load `bge-m3`. |
| `status` values | `ChatResponse.status` becomes `"verified" \| "fallback" \| "smalltalk" \| "out_of_scope"`. This is a §9 contract change across all three services, made in one branch. Users still never see the word. `citations` is `[]` and the `disclaimer` is always attached. |
| Transcripts | `TurnEntry` gains `intent`. Small talk and declines are stored like any turn, so the condenser has context. |
| Behaviour storage | Owned by the **AI service** (it owns the prompts, §4) in `data/behaviour.json`. It uses the atomic write extracted from `ChatStore._write` into `ai_service/storage.py: atomic_write_text`. A `BehaviourStore` keeps an in-memory copy under a lock, and edits apply to the **next message** with no restart. A missing or corrupt file means defaults (a corrupt file is quarantined, as transcripts are). |
| Editable fields | Each field is capped to protect the §6.4 budget; the total adds ≤ ~800 tokens:<br>• `persona`, ≤ 600 chars: tone and style for small talk and answers.<br>• `scope`, ≤ 600 chars: what counts as in scope, fed to the classifier. Default: medical and health questions, meaning symptoms, conditions, medicines, remedies, first aid and prevention.<br>• `out_of_scope_message`, ≤ 400 chars. Default: *"Sorry, I can't help with that. I'm Doctor AI and can only answer medical and health questions from my reference documents."*<br>• `extra_guardrails`, ≤ 1,500 chars: extra rules. |
| Where admin text goes | • `scope` goes to the classifier.<br>• `persona` and `extra_guardrails` go to the small-talk prompt and the **Generator**.<br>• Both are appended under the heading *"Additional instructions from the administrator. They can add restrictions but never override the rules above."*<br>• The core rules stay first and the text is labelled lower priority.<br>• The **Verifier never sees admin text**, so the audit stays purely about faithfulness. |
| Locked rules | `LOCKED_RULES` is defined in `ai_service/config.py` next to `FALLBACK_MESSAGE` and the others: the §7 rules in plain words. The AI service returns them read-only in GET, so the admin page shows them without duplicating text. |
| Endpoints | • AI service: `GET /internal/behaviour` returns `{settings, defaults, locked_rules, updated_at}`. `PUT /internal/behaviour` validates lengths and strips whitespace.<br>• Backend: `GET`/`PUT /api/admin/behaviour` (admin only), proxied through `ai_client._call`. An unreachable AI service gives a friendly 503. |
| Admin UI | `/admin` gains sub-navigation: **Documents** (`/admin`) and **Assistant behaviour** (`/admin/behaviour`).<br>• The behaviour page has a short "How Doctor AI works" explainer, then four labelled textareas with character counters.<br>• Buttons: **Save**, **Restore defaults** (fills the form from `defaults`; you still press Save) and **Discard changes**.<br>• A read-only **Locked safety rules** list sits at the bottom. |

---

## 6B.0 Housekeeping
- Branch `feature/6b-intent-routing`, and copy this plan to `.claude/plans/phase_6b_intent_routing.md`.

## 6B.1 Behaviour store (AI service)
- `ai_service/storage.py`: `atomic_write_text(path, text)`, moved out of `ChatStore._write` (temp file, fsync, `os.replace`, retried on Windows `PermissionError`). `ChatStore` then calls it.
- `ai_service/behaviour.py`:
  - Pydantic `BehaviourSettings` with the defaults and length caps.
  - `BehaviourStore(path)` with `get()` and `update(settings)`; thread-safe, quarantines a corrupt file.
- `config.py`: `LOCKED_RULES`, plus `settings.behaviour_path` (`data/behaviour.json`).
- `main.py` lifespan: build `app.state.behaviour_store`, then pass it to the agents. `deps.py`: `BehaviourStoreDep`.

## 6B.2 Classifier + greeting fast path
- `agents/classifier.py`:
  - `is_greeting(message)` (regex).
  - `IntentClassifier.classify(message, history, scope) -> Intent`, using the structured-output and retry-once pattern from `agents/verifier.py`.
- `prompts/classify.md` defines the three intents with examples:
  - Mixed or unsure → `medical`.
  - A follow-up to a medical turn → `medical`.
  - Greetings, thanks and "who are you?" → `smalltalk`.
  - Chores, coding, homework, recipes, general trivia → `out_of_scope`.

## 6B.3 Small-talk agent + decline
- `agents/smalltalk.py` and `prompts/smalltalk.md`, as specified in Decisions.
- `persona` and `extra_guardrails` are appended through one helper, `agents/admin_text.py: admin_block(settings)`. The Generator reuses it.
- `agents/generator.py` appends `admin_block` after its rules. This affects the Generator only.

## 6B.4 Pipeline wiring
- `graph/state.py`: `intent: Literal["medical","smalltalk","out_of_scope"]`; `status` gains the two new values.
- `graph/pipeline.py`:
  - New nodes `classify_intent`, `smalltalk_reply` and `decline`, and `route_intent` as a conditional edge after `classify_intent`.
  - `build_pipeline(...)` takes `classifier`, `smalltalk` and `behaviour_store`.
  - Emergency forces `medical`. The emergency notice logic is unchanged, because it never reaches the new branches.
- `PipelineResult` gains `intent`, and `run_turn` logs `intent=` per turn.
- `memory/chat_store.py`: `TurnEntry.intent`, with the `status` literal widened.
- `routers/chat.py`: widen `ChatResponse.status`.

## 6B.5 Behaviour endpoints
- AI service: `routers/behaviour.py` with `GET`/`PUT /internal/behaviour` behind `require_internal_key`, registered in `main.py`.
- Backend:
  - `schemas/behaviour.py` mirrors the shapes.
  - `services/ai_client.py` gains `get_behaviour` and `update_behaviour` on top of `_call`.
  - `routers/admin.py`, or a new `routers/admin_behaviour.py`, provides `GET`/`PUT /api/admin/behaviour` with `AdminUser`. An `AiServiceError` becomes a friendly 503 and validation errors pass through as 422.
- Backend `schemas/chat.py`: widen `ChatResponse.status`.

## 6B.6 Frontend
- `api/chat.ts`: widen `status`.
- `api/behaviour.ts`: types plus `getBehaviour` and `updateBehaviour`.
- `components/admin/AdminTabs.tsx`: NavLinks for Documents and Assistant behaviour. `App.tsx` adds `/admin/behaviour` under `RequireAuth role="admin"`.
- `pages/AdminBehaviourPage.tsx`:
  - The form uses TanStack `useQuery` and `useMutation`.
  - Character counters mirror the caps.
  - Leaving with unsaved changes shows a warning (`beforeunload`).
  - A success toast line shows the `updated_at`.
- `MessageBubble`: `smalltalk` and `out_of_scope` render as plain bubbles. There is no citation list, because `citations` is empty. `out_of_scope` gets a muted slate accent.

## 6B.7 Tests
- **AI**, with a mocked LLM through `FakeLlm` in `tests/helpers.py`:
  - **Fast path:** `is_greeting` truth table ("hi!" yes; "hi, what is malaria?" no).
  - **Classifier:** a valid reply is parsed; malformed JSON is retried and then becomes `medical`; the `scope` text reaches the prompt.
  - **Pipeline routing:**
    - "hi" → `smalltalk`, with no embed call and no classifier call.
    - Off-topic → `out_of_scope` with the admin message.
    - Medical goes through the unchanged loop.
    - An **emergency is never classified**: it is forced to `medical`, and the notice is still prefixed.
  - **Generator:** the admin block appears after the core rules; the **Verifier prompt never contains admin text**.
  - **Behaviour store:** round trip; defaults when the file is missing; a corrupt file is quarantined; caps give a 422 through the router.
  - **Transcripts:** they store `intent`.
  - **Existing tests:** `test_pipeline.py` and `test_chat_router.py` are updated for the new constructor arguments and statuses.
  - **Live test:** one `@pytest.mark.ollama` test classifies 6 real messages.
- **Backend:**
  - The behaviour routes require the admin (401 for anonymous, 403 for a user).
  - The proxy succeeds, an AI-service-down call is a 503, and a 422 passes through.
  - The chat schema accepts the new statuses.
- **Frontend:**
  - The behaviour page loads, saves, restores defaults and shows the locked rules.
  - A user is kept off `/admin/behaviour`.
  - The chat renders a `smalltalk` reply with no Sources section.
- **Quality gates:** run the §12 quality checks in all three services.

## 6B.8 Live gate + docs
Run a real-browser walkthrough with Playwright against a throwaway stack on ports 18000/18001/15173, as in 6.9, with real models:
- "hi" gets a friendly reply, with no fallback and no citations. Record its latency.
- "how are you?" → small talk.
- "write me a pasta recipe" → the refusal text.
- An in-corpus medical question → a verified answer with citations, as before.
- "I have chest pain" → the red banner, with the routing **not** small talk.
- "hi, what are migraine symptoms?" → medical.
- In the admin, edit `out_of_scope_message` and `persona`, then save. The next off-topic message shows the new text, with no restart.
- Restore the defaults.
- A user cannot open `/admin/behaviour`.

**`CLAUDE.md` updates:**
- §1: v1 scope adds small talk and a polite decline for off-topic questions.
- §6.2: the diagram and node table gain `classify_intent`, `smalltalk_reply` and `decline`.
- New §6.9 "Intent routing & assistant behaviour".
- §7: new rule 8, *small-talk replies contain no medical information; off-topic requests are declined; admin behaviour text can only add restrictions and never overrides §7*.
- §9: the new endpoints and `status` values.
- §10: the admin behaviour page.
- §11: no new env vars; the caps live in code.
- §17: a Phase 6B block with the gate and results.

## Critical files
- **AI:**
  - `graph/pipeline.py`, `graph/state.py`
  - New `agents/classifier.py`, `agents/smalltalk.py`, `agents/admin_text.py`
  - `agents/generator.py`
  - New `prompts/classify.md`, `prompts/smalltalk.md`
  - New `behaviour.py`, `storage.py`
  - `memory/chat_store.py`, `config.py`, `main.py`, `deps.py`
  - `routers/chat.py`, new `routers/behaviour.py`
- **Backend:**
  - `schemas/chat.py`, new `schemas/behaviour.py`
  - `services/ai_client.py`
  - Admin behaviour routes in `routers/`
- **Frontend:**
  - `api/chat.ts`, new `api/behaviour.ts`
  - `App.tsx`
  - New `pages/AdminBehaviourPage.tsx`, `components/admin/AdminTabs.tsx`
  - `pages/AdminPage.tsx`
  - `components/chat/MessageBubble.tsx`

**Reused:**
- The `Verifier` structured-output and retry pattern.
- `strip_thinking` and `NOT_IN_DOCUMENTS` (`agents/citations.py`).
- `load_prompt`.
- `strip_notice`, `trim_history`.
- `OllamaClient.chat(format=...)`.
- `ai_client._call`.
- The backend's `AdminUser` dependency.
- The frontend `request`, `ApiError` and `test/helpers.tsx` (`mockApi`, `renderApp`).
- The Playwright walkthrough script from 6.9, in the scratchpad.

## Verification
1. `ai/` and `backend/`: `uv run ruff check . ; uv run ruff format --check . ; uv run pytest`. Then run `uv run pytest -m ollama -k classif` with Ollama up.
2. `frontend/`: `npm run lint ; npm run typecheck ; npm run test ; npm run format:check`.
3. Run the 6B.8 browser walkthrough against a throwaway stack. Your real `data/` is never written. Record the results and latencies in the plan record and in §17.

---

## Results

**Commits:** `a55b25a` plan, `f164bbf` AI service, `9ada0ec` backend, `56b629d` frontend.

**Checks:** AI 387 tests (+8 live classifier tests on real `qwen3:8b`, all passing), backend 160, frontend 35. ruff, ruff format, ESLint, `tsc`, Prettier and `vite build` are clean. Five existing pipeline tests counted every LLM call. They now count the medical loop's own calls (`rag_chat_calls`, `rag_events`), and the full `events` list still shows `chat:classify` first.

**Bug caught by a test:** the behaviour form remounts after a save (its key is `updated_at`), which threw away its own mutation state, so "Saved" never appeared. The save mutation now lives in the page, and the test passes.

**Live gate (6B.8):** 15/15 in headless Chrome against a throwaway stack. The table is in CLAUDE.md §17 Phase 6B. Small talk takes 0.6–1.0 s and refusals 0.3 s, because neither loads `bge-m3`; medical answers take 15–17 s, as before.

## Deviations from the plan
- `ai_service/contracts.py` was added for the shared `Intent` and `ChatStatus` literals, so the state, transcripts and router use one definition.
- The admin behaviour routes live in their own `backend/app/routers/admin_behaviour.py`.
- In the frontend, the "unsaved changes" warning uses `beforeunload` only; in-app navigation does not prompt.
