# CLAUDE.md — VeriDoc AI

This file guides Claude (and any developer) working in this repository. Read it fully before making changes. If a decision here needs to change, ask first, then update this file in the same change.

---

## 1. Project overview

**VeriDoc AI** is a self-correcting **dual-agent RAG** (Retrieval-Augmented Generation) system. A **Generator** agent drafts answers from retrieved document chunks; a **Verifier** agent audits each draft for faithfulness and relevance. If the draft fails, context retrieval is **escalated** (wider Top-K, then parent chunks) for up to **3 iterations**, after which the system returns a **deterministic safe fallback** instead of guessing.

B.Tech CSE Major Project — Rishi Raj Anand (23103121), Vedant Salve (23117040). Mentor: Dr. Jagdeep Kaur.

### Current scope: v1 "Doctor AI"
The first version is a medical-information assistant:
1. The **admin** uploads medical reference documents (the knowledge base).
2. **Users** register, log in, and chat with Doctor AI, which suggests medication or other remedies **only from the uploaded documents**, with citations.
3. Doctor AI remembers earlier messages **within the same chat session**. There is **no chat history UI** — every visit starts a fresh session.
4. Users see **only the final verified answer** (no live agent states, no scores).

### Hard constraints
- **Text only.** Supported uploads: **PDF, TXT, MD, DOCX**. No images, audio, video, or scanned/OCR PDFs — reject them with a clear error.
- **All AI runs locally through Ollama** (generation + embeddings). Never add OpenAI, Anthropic, or any cloud LLM/embedding API.
- **Exactly one admin account**, created at startup from environment variables. Users cannot become admin.
- **Windows + PowerShell** is the development environment. All commands and paths must work there.

### Out of scope for v1 (do not build unless asked)
Multimodal ingestion, OCR, chat history list, multiple admins, streaming responses, Custom Agent Studio, public headless REST API, Docker, cloud deployment.

---

## 2. Target hardware (design for this)

| Component | Spec |
|---|---|
| GPU | NVIDIA RTX 5050 Laptop, **8 GB VRAM** |
| RAM | 16 GB |
| CPU | AMD Ryzen 7 250 |
| OS | Windows 64-bit |

Measured on this machine (2026-11-21, Ollama 0.40.13):

| | Resident VRAM | Notes |
|---|---|---|
| Total GPU | 8151 MiB | the laptop GPU also drives the display |
| `qwen3:8b` @ `num_ctx = 8192` | **6.2 GB** | 100% GPU, ~50–60 tok/s warm |
| `bge-m3` | **689 MB** | 100% GPU, **1024-dim** embeddings |

Implications:
- One LLM loaded at a time. **Generator and Verifier use the same model** with different prompts (avoids model swapping in VRAM).
- **The LLM and the embedding model cannot be co-resident.** 6.2 GB + 689 MB against ~8 GB total means Ollama evicts one whenever the other is called. Each eviction costs **~7 s** to reload. This is accepted, not worked around — see §6.8.
- Context window is fixed at **`num_ctx = 8192`**, always set explicitly in every Ollama call (Ollama's default is smaller and silently truncates).
- Prompt budget must stay under ~7,000 tokens (see §6.4).

---

## 3. Tech stack

| Layer | Technology |
|---|---|
| Frontend | React + Vite + **TypeScript (strict)**, Tailwind CSS, React Router, TanStack Query |
| Backend | Python 3.12, **FastAPI**, Pydantic v2, SQLAlchemy 2.0, Alembic, SQLite |
| Auth | JWT access tokens (`pyjwt`), Argon2 password hashing (`argon2-cffi`), roles `user` / `admin` |
| AI service | Python 3.12, **FastAPI**, **LangGraph**, `ollama` Python client |
| Vector DB | **Qdrant local (embedded) mode** — `QdrantClient(path=...)` |
| Parsing | PyMuPDF (PDF), `python-docx` (DOCX), plain read (TXT/MD) |
| LLM | Ollama **`qwen3:8b`** (Generator + Verifier). Fallback: `qwen3:4b` |
| Embeddings | Ollama **`bge-m3`** |
| Evaluation | **RAGAS** (offline script, Ollama as judge) |
| Python tooling | `uv` (deps/venv), `ruff` (lint + format), `pytest`, `httpx` |
| Frontend tooling | npm, ESLint, Prettier, Vitest |

Model names, thresholds, and Top-K values live in config/env — never hardcode them in logic.

**Pinned versions** (surveyed 2026-11-21; exact pins live in the three manifests). Where a pin
differs from the prose above, this table wins:

| Pin | Why it differs |
|---|---|
| SQLAlchemy **2.1.9** | current release of the 2.x line written as "2.0" above; same modern API |
| TypeScript **6.0.3**, not 7.0.2 | `typescript-eslint@8.73.0` peers on `typescript >=4.8.4 <6.1.0`. On TS 7 every TypeScript lint rule silently switches off, including the §14 "no `any`" rule. **Bump only when that peer range widens.** |
| Tailwind CSS **4.3.5** | v4 is CSS-first: the `@tailwindcss/vite` plugin plus `@import "tailwindcss";` in `index.css`. **There is no `tailwind.config.js`** — theme overrides go in CSS `@theme` blocks. |
| `react-router` **8.5.2** | since v7 `react-router` is the package to import; `react-router-dom` is a deprecated re-export |
| Vite **8.3.8**, React **19.3.1** | current majors |
| `email-validator` **2.4.0** | Pydantic's `EmailStr` needs it (pulls `dnspython` + `idna`); syntax-only, no DNS lookups on the request path. It rejects special-use domains such as `.local`, so **login deliberately takes a plain `str`** — the seeded admin may use `admin@veridoc.local` |
| `ragas` **0.4.4** | kept in an optional `eval` dependency group (`uv sync --group eval`), not the default AI deps — it pulls in langchain and datasets and is only needed for task 7.2 |

The frontend uses a **single `tsconfig.json`** rather than Vite's three-file project-reference
layout, because `tsc --noEmit` (what `npm run typecheck` runs) conflicts with the
`composite: true` that project references require.

---

## 4. Architecture

```
┌──────────────────────────┐
│ frontend  (React, :5173) │
└────────────┬─────────────┘
             │ REST + JWT (Authorization: Bearer)
┌────────────▼─────────────┐      ┌──────────────────────┐
│ backend  (FastAPI, :8000)│──────│ SQLite  data/app.db  │
│ auth, users, admin,      │      │ uploads data/uploads │
│ documents, chat gateway  │      └──────────────────────┘
└────────────┬─────────────┘
             │ HTTP + X-Internal-Key (localhost only)
┌────────────▼─────────────┐      ┌──────────────────────┐
│ ai service (FastAPI,:8001)│─────│ Qdrant  data/vectors │
│ ingest, retrieval,       │      │ docstore data/docstore.db
│ LangGraph agents, eval   │      │ chats   data/chats/  │
└────────────┬─────────────┘      └──────────────────────┘
             │
┌────────────▼─────────────┐
│ Ollama (:11434)          │  qwen3:8b, bge-m3
└──────────────────────────┘
```

### Ownership rules (keep these boundaries)
- **Frontend** talks **only** to the backend. Never to the AI service or Ollama.
- **Backend** owns users, auth, the admin account, file uploads, the `documents` table, and chat-session ownership. It never imports AI code and never calls Ollama.
- **AI service** owns parsing, chunking, embeddings, Qdrant, the parent-chunk docstore, the LangGraph pipeline, chat transcripts, and evaluation. It never touches `app.db` and has no user auth — it trusts only requests carrying the correct `X-Internal-Key`, and binds to `127.0.0.1`.
- Qdrant local mode allows **one process only** — only the AI service opens `data/vectors/`.

---

## 5. Repository layout

```
veridoc/
├── CLAUDE.md
├── README.md
├── .gitignore                 # must ignore data/, .env, .venv/, node_modules/
├── .gitattributes             # * text=auto
├── data/                      # runtime data, gitignored (created on startup)
│   ├── app.db                 # backend SQLite
│   ├── uploads/               # original admin-uploaded files (<doc_id>_<safe_name>)
│   ├── vectors/               # Qdrant local storage
│   ├── docstore.db            # AI service: parent chunks
│   ├── chats/                 # <session_id>.json transcripts
│   └── eval/                  # RAGAS result CSVs
├── frontend/
│   ├── .env.example           # VITE_API_URL
│   └── src/
│       ├── api/               # ALL fetch calls + response types live here
│       ├── components/
│       ├── pages/             # LoginPage, RegisterPage, ChatPage, AdminPage
│       ├── hooks/
│       ├── context/           # AuthContext
│       └── main.tsx
├── backend/
│   ├── pyproject.toml
│   ├── .python-version        # 3.12, provisioned by uv
│   ├── .env.example
│   ├── alembic/
│   ├── app/
│   │   ├── main.py            # app factory, startup (create dirs, seed admin)
│   │   ├── config.py          # pydantic-settings
│   │   ├── db.py
│   │   ├── models/            # SQLAlchemy models
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── routers/           # auth.py, chat.py, admin.py, health.py
│   │   ├── services/          # auth_service, document_service, ai_client
│   │   └── deps.py            # get_db, get_current_user, require_admin
│   └── tests/
└── ai/
    ├── pyproject.toml
    ├── .python-version       # 3.12, provisioned by uv
    ├── .env.example
    ├── ai_service/
    │   ├── main.py
    │   ├── config.py
    │   ├── security.py        # X-Internal-Key check
    │   ├── deps.py            # FastAPI deps exposing the objects lifespan builds
    │   ├── routers/           # ingest.py, chat.py, health.py
    │   ├── ingestion/         # parsers.py, chunker.py, indexer.py
    │   ├── retrieval/         # vector_store.py, docstore.py, retriever.py
    │   ├── agents/            # generator.py, verifier.py, condenser.py, safety.py
    │   ├── graph/             # state.py, pipeline.py (LangGraph)
    │   ├── prompts/           # *.md prompt templates (never inline long prompts)
    │   ├── llm/               # ollama_client.py (single wrapper for all calls)
    │   ├── memory/            # chat_store.py (data/chats)
    │   └── eval/              # run_ragas.py, testset.jsonl
    └── tests/
```

---

## 6. AI pipeline specification

### 6.1 Ingestion (admin upload)
1. Backend receives the upload, validates extension (`.pdf .txt .md .docx`) and size (`MAX_UPLOAD_MB`), computes SHA-256, rejects duplicates, saves to `data/uploads/`, inserts a `documents` row with `status="pending"`.
2. Backend (FastAPI `BackgroundTasks`) calls AI `POST /internal/ingest` with a long timeout.
3. AI service parses to text **with page numbers** (PDF pages; DOCX/MD → a heading-based **section index**: section 1 until the first heading, then one per Heading 1/2 or `#`/`##`; TXT, or any file without headings → page 1). If a PDF yields almost no text, fail with `"scanned or image-only PDF is not supported"`.
4. **Chunking (parent–child):**
   - Parent chunks ≈ **800 tokens**, split on paragraph/heading boundaries where possible.
   - Child chunks ≈ **300 tokens** with **50 token overlap**, each linked to its `parent_id`.
   - **Markdown tables are split on row boundaries**, never mid-row: every table chunk is a complete table (heading directly above it + header row + separator + whole rows), with no overlap. A row longer than a chunk is cut at whitespace rather than dropped.
   - Token estimate: `len(text) / 4` (no tokenizer dependency) unless changed deliberately.
5. Embed **child chunks only** with `bge-m3` (**vector size 1024, cosine distance** — measured, see §2); upsert to Qdrant collection `chunks` with payload `{doc_id, filename, page, parent_id, chunk_index, text}`.
6. Store parent chunks in `data/docstore.db` keyed by `parent_id` (with `doc_id`, `filename`, `page_start`, `page_end`, `text`).
7. Return `chunk_count`; backend sets `status="ready"` (or `"failed"` + `error`).
8. Deleting a document removes its file, its Qdrant points (filter by `doc_id`), and its docstore parents.

### 6.2 Query pipeline (LangGraph state machine)

```
safety_check → condense_query → retrieve → generate → verify
                                   ▲                    │
                                   │   fail & iter < 3  │
                                   └──── escalate ◄─────┤
                                                        │ pass          → finalize
                                                        │ fail & iter=3 → fallback
```

| Node | Behaviour |
|---|---|
| `safety_check` | Deterministic keyword/regex match (list in `ai_service/agents/safety_terms.yaml`) for red-flag emergencies (chest pain, difficulty breathing, stroke signs, severe bleeding, suicidal thoughts, poisoning/overdose, unconsciousness, etc.). If matched, set `emergency=true` — the final response is **prefixed** with the emergency notice (§7). The pipeline still runs. |
| `condense_query` | If the session has prior turns, rewrite the latest message into a **standalone question** using the last turns (e.g. "what about for children?" → "What is the treatment for migraine in children?"). First message passes through unchanged. |
| `retrieve` | Behaviour depends on `iteration` (§6.3). |
| `generate` | Generator answers **only** from the provided context, cites every claim as `[n]` mapped to sources. If the context lacks the answer, it must say so. |
| `verify` | Verifier audits the draft against the **retrieved context only** (never against chat history) and returns strict JSON (§6.5). |
| `escalate` | `iteration += 1`; append the Verifier's `missing_info` to the retrieval query. |
| `finalize` | Attach citations + disclaimer, save turn to `data/chats/`, return `status="verified"`. |
| `fallback` | Return the fixed fallback message (§7), save turn, `status="fallback"`. Never return an unverified draft. |

### 6.3 Escalation levels

| Iteration | Mode | Retrieval | Context sent to Generator |
|---|---|---|---|
| 1 | Precision | Top-**3** child chunks | the 3 child chunks |
| 2 | Expansion | Top-**8** child chunks → their parents | ≤ **4** unique parents |
| 3 | Expansion | Top-**15** child chunks → their parents | ≤ **6** unique parents |

- After 3 failed verifications → `fallback`. **Never more than 3 iterations.**
- If the best similarity score is below `MIN_RETRIEVAL_SCORE` at iteration 1, skip straight to iteration 2; if still below at iteration 3, go to `fallback` (question is outside the knowledge base).
- Parents are de-duplicated and ordered by best child score.
- Iteration 2 **generates even when the best score is under the gate** and lets the Verifier judge; only the *last* iteration falls back on a low score, and so does a retrieval that found nothing at all (empty knowledge base, no LLM call).
- A `NOT_IN_DOCUMENTS` or uncited draft counts as a failed iteration (the Verifier is not called): escalate, or fall back on the last iteration.
- The retrieval query is the question plus the **latest** `missing_info` (not an accumulation). The query vector is reused when the text is unchanged, so there is no needless `bge-m3` swap. The Generator and Verifier always see the original question.

### 6.4 Prompt budget (8,192 ctx)
| Part | Max tokens |
|---|---|
| System prompt + rules | ~800 |
| Chat history (last 6 messages) | ~1,500 |
| Retrieved context | ~4,800 |
| Answer | ~1,000 |
Trim history first, then lowest-scoring context, never the system prompt.

### 6.5 Verifier contract
The Verifier is called with Ollama structured outputs (`format=<JSON schema>`), `temperature=0`. Schema:

```json
{
  "faithfulness": 0.0,
  "relevance": 0.0,
  "unsupported_claims": ["..."],
  "missing_info": "string or null",
  "verdict": "pass | fail"
}
```

**Pass rule is computed in code**, not trusted from `verdict` alone:
`passed = verdict == "pass" and faithfulness >= FAITHFULNESS_THRESHOLD (0.85) and relevance >= RELEVANCE_THRESHOLD (0.75) and not unsupported_claims`.
If the JSON fails to parse/validate, retry once, then treat as `fail`.

### 6.6 LLM call settings
- All Ollama calls go through `ai_service/llm/ollama_client.py`. No direct `ollama.*` calls elsewhere.
- Always pass `options={"num_ctx": NUM_CTX, "temperature": ...}`.
- Generator `temperature=0.2`; Verifier and Condenser `temperature=0`.
- Disable Qwen3 "thinking" mode for all calls (`think=False`) unless deliberately testing it — it costs tokens and latency.
- Keep the model warm with `keep_alive` (e.g. `"30m"`).

### 6.7 Session memory
- The frontend creates a new `session_id` (UUID v4) each time the chat page opens; it is kept in React state only (lost on refresh, by design).
- Backend records `(session_id, user_id)` on the first message and rejects use of another user's session.
- AI service stores each turn in `data/chats/<session_id>.json`:
  `{session_id, user_id, created_at, turns: [{role, content, standalone_query, status, iterations, citations, verifier_scores, timestamp}]}`.
- Write the transcript atomically (write temp file, then replace).
- Scores and iteration counts are stored for evaluation but **never shown to users**.

### 6.8 Model residency and latency (8 GB VRAM)

`qwen3:8b` and `bge-m3` evict each other (§2), so every model switch costs ~7 s. The models
stay as specified in §3 — the latency is accepted in exchange for answer quality, since this
is a non-streaming UI with a "Thinking…" indicator. Rules:

- **Minimise switches, don't avoid them.** One switch pair per retrieval round is the floor:
  `condense` (LLM) → `retrieve` (embed) → `generate` + `verify` (LLM). Keep `generate` and
  `verify` adjacent so they share one residency.
- **Never interleave** an embedding call between the Generator and the Verifier.
- Each escalation adds one more switch pair, so worst case (3 iterations) is ~40 s of model
  loading on top of inference. Budget 30–90 s per answer and keep the backend/AI HTTP
  timeouts well above that.
- During ingestion only `bge-m3` is needed — embed all child chunks in one batched pass so
  the LLM is never swapped in mid-ingest.
- Qdrant's `data/vectors/.lock` is advisory and **does not go stale**: the AI service was
  hard-killed and restarted successfully (verified 2026-11-21), so a crashed service needs no
  manual lock cleanup. The single-writer rule still holds for *concurrent* processes.
- If this latency proves unacceptable during the 4.8 calibration pass, the documented escape
  hatch is the §3 fallback `qwen3:4b` (~3.3 GB, co-resident with `bge-m3`, no swapping) at the
  cost of a weaker Verifier. Changing it requires updating §3 and §11 in the same change.

---

## 7. Medical safety rules (non-negotiable)

1. **Grounded only.** Every medical statement must come from the uploaded documents and carry a citation. No answers from the model's general knowledge.
2. **Emergency first.** When `safety_check` matches, the response begins with:
   > ⚠️ This may be a medical emergency. Call **112** (or **108** for an ambulance in India) or go to the nearest emergency department now.
3. **Dosage caution.** Doses, pediatric use, pregnancy/breastfeeding, and drug interactions are stated **only** if a retrieved document states them explicitly, quoted with its citation. Otherwise say the documents don't cover it and advise consulting a doctor/pharmacist.
4. **Fallback text** (exact, configurable in one place):
   > I couldn't find verified information in my medical references to answer this safely. Please consult a qualified doctor.
5. **Disclaimer** shown on every answer and as a persistent banner in the chat UI:
   > VeriDoc AI provides information from reference documents and is not a substitute for professional medical advice, diagnosis, or treatment.
6. Never present output as a diagnosis. Phrase as "the references suggest…".
7. Do not weaken, bypass, or remove these rules (including for testing) without explicit approval.

---

## 8. Data model (backend SQLite)

```
users
  id (int pk) | email (unique) | password_hash | role ('user'|'admin') | created_at

documents
  id (int pk) | filename | stored_path | sha256 (unique) | size_bytes
  | status ('pending'|'ready'|'failed') | chunk_count | error (nullable)
  | uploaded_by (fk users.id) | created_at

chat_sessions
  id (uuid pk) | user_id (fk users.id) | created_at
```

- Schema changes go through **Alembic migrations** only — never edit the DB by hand.
- Invariants enforced **in the schema**, not just in code: `CHECK (role IN ('user','admin'))`, a partial unique index `uq_users_single_admin` on `role WHERE role = 'admin'` (so a second admin is impossible), `CHECK` on `documents.status`, and `PRAGMA foreign_keys=ON` on every connection (SQLite ignores FKs otherwise). Emails are stored lower-cased.
- The admin is seeded/updated on startup from `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Registration always creates `role='user'`. There is exactly one admin.

---

## 9. API contracts

### Backend (`http://localhost:8000`) — public to frontend
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | — | `{email, password}` → user |
| POST | `/api/auth/login` | — | `{email, password}` → `{access_token, token_type, role}` |
| GET | `/api/auth/me` | user | current user |
| POST | `/api/chat/message` | user | `{session_id, message}` → `ChatResponse`. `message` is 1–2000 characters. Any signed-in user may chat, the admin included. **401** anonymous, **403** a session owned by another user, **422** bad UUID / empty / over-long message, **503** (friendly text) when the AI service fails |
| POST | `/api/admin/documents` | admin | multipart upload → document (status `pending`). Rejections: **415** wrong extension or content not matching it, **413** over `MAX_UPLOAD_MB`, **422** empty, **409** duplicate SHA-256 |
| GET | `/api/admin/documents` | admin | list documents with status |
| DELETE | `/api/admin/documents/{id}` | admin | delete file + vectors + row → 204. **409** while the document is `pending`; **503** (nothing removed) if the AI service cannot delete its index |
| GET | `/api/health` | — | backend + AI service health |

`ChatResponse`:
```json
{
  "answer": "markdown text with [1] [2] citations",
  "citations": [{"n": 1, "filename": "who_guidelines.pdf", "page": 12}],
  "status": "verified | fallback",
  "emergency": false,
  "disclaimer": "..."
}
```

### AI service (`http://127.0.0.1:8001`) — internal only, header `X-Internal-Key` required
| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/ingest` | `{doc_id, file_path, filename}` → `{chunk_count}` |
| DELETE | `/internal/documents/{doc_id}` | remove vectors + parents → `{deleted_points, deleted_parents}`; idempotent |
| POST | `/internal/chat` | `{session_id, user_id, message}` → `ChatResponse` |
| GET | `/internal/health` | Ollama reachable, models pulled, Qdrant open |

Errors use FastAPI's standard `{"detail": "..."}` with proper status codes. The backend maps AI-service failures to `503` with a friendly message.

---

## 10. Frontend

- Pages: `/login`, `/register`, `/chat` (user), `/admin` (admin only — upload, list with status, delete). Route guards by role.
- JWT stored in `localStorage` for v1 (known trade-off; revisit before any public deployment).
- All HTTP calls live in `src/api/`; components never call `fetch` directly. Types for every request/response.
- Chat UI: message list, input box, "Thinking…" indicator while waiting, citation list under each answer, red emergency banner when `emergency=true`, persistent disclaimer banner.
- Render answers as Markdown with a sanitizing renderer (`react-markdown`, no raw HTML).
- No history sidebar. Refreshing the page starts a new session.

---

## 11. Environment variables

`backend/.env` (copy from `.env.example`):
```
DATA_DIR=../data
JWT_SECRET=change-me
JWT_EXPIRE_MINUTES=120
ADMIN_EMAIL=admin@veridoc.local
ADMIN_PASSWORD=change-me
AI_SERVICE_URL=http://127.0.0.1:8001
INTERNAL_API_KEY=change-me-shared-secret
MAX_UPLOAD_MB=25
CORS_ORIGINS=http://localhost:5173
```

`ai/.env`:
```
DATA_DIR=../data
INTERNAL_API_KEY=change-me-shared-secret
OLLAMA_HOST=http://127.0.0.1:11434
LLM_MODEL=qwen3:8b
EMBED_MODEL=bge-m3
NUM_CTX=8192
FAITHFULNESS_THRESHOLD=0.85
RELEVANCE_THRESHOLD=0.75
MIN_RETRIEVAL_SCORE=0.35
MAX_ITERATIONS=3
HISTORY_MAX_MESSAGES=6
TOP_K_PRECISION=3
TOP_K_LEVEL2=8
MAX_PARENTS_LEVEL2=4
TOP_K_LEVEL3=15
MAX_PARENTS_LEVEL3=6
CONTEXT_MAX_TOKENS=4800
HISTORY_MAX_TOKENS=1500
PARENT_CHUNK_TOKENS=800
CHILD_CHUNK_TOKENS=300
CHILD_CHUNK_OVERLAP_TOKENS=50
EMBED_DIM=1024
```

The last four describe the shape of what is already indexed: changing any of them means re-ingesting every document (an `EMBED_DIM` mismatch makes the AI service refuse to start and names the fix).

`frontend/.env`:
```
VITE_API_URL=http://localhost:8000
```

Never commit `.env` files. Keep `.env.example` files up to date whenever a variable is added.
Phase 1 (auth) needed no new variables; `JWT_SECRET` should be at least 32 bytes.

---

## 12. Commands (Windows PowerShell)

> Use `;` to chain commands (Windows PowerShell 5.1 has no `&&`). Run each service in its own terminal.

**One-time setup**
```powershell
# Install uv (Python package manager). Open a NEW terminal afterwards: the
# installer edits PATH and the current shell will not see it.
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Pull models (Ollama for Windows must be installed and running)
ollama pull qwen3:8b
ollama pull bge-m3
```

**Backend**
```powershell
cd backend
uv sync
uv run alembic upgrade head   # required once before the first run, and after pulling new migrations
uv run uvicorn app.main:app --reload --port 8000
```

> The backend **refuses to start** against a database with no tables and names the fix
> (`uv run alembic upgrade head`), rather than half-working.
>
> The system Python on this machine is 3.14, which the project does not use. Both
> `pyproject.toml` files pin `requires-python = ">=3.12,<3.13"` and carry a `.python-version`,
> so `uv sync` downloads and uses its own CPython 3.12. Run every Python command through
> `uv run` — never a bare `python`.
>
> `DATA_DIR=../data` is relative to the **working directory**, so start each service from its
> own folder (`backend/`, `ai/`). Both log the resolved absolute path at startup; if that path
> looks wrong, the working directory is wrong.

**AI service** — never use `--reload` here
```powershell
cd ai
uv sync
uv run uvicorn ai_service.main:app --host 127.0.0.1 --port 8001
```
Qdrant local mode holds an exclusive lock on `data/vectors/`. A uvicorn reload can
fail to reopen that storage and leaves the service broken until a full restart, so the
AI service always runs without `--reload` — restart it manually after changing its code.
(The backend keeps `--reload`; it has no such lock.)

**Frontend**
```powershell
cd frontend
npm install
npm run dev
```

**Quality checks (run before saying a task is done)**
```powershell
# in backend/ and ai/
uv run ruff check . ; uv run ruff format --check . ; uv run pytest

# in frontend/
npm run lint ; npm run typecheck ; npm run test
```

**New migration**
```powershell
cd backend
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

**Check GPU usage** — `ollama ps` should show the model at `100% GPU`. If it shows CPU, the context or model is too large for 8 GB.

**RAGAS evaluation**
```powershell
cd ai
uv run python -m ai_service.eval.run_ragas
```

---

## 13. Evaluation (RAGAS)

- Test set: `ai/ai_service/eval/testset.jsonl`, 30–50 lines, written by the team from the uploaded documents:
  `{"question": "...", "reference": "ground-truth answer", "source": "filename.pdf"}`.
- The script runs each question through the **same pipeline as production** (call the LangGraph pipeline directly, not over HTTP), collecting answer + retrieved contexts.
- Metrics: **Faithfulness**, **Answer/Response Relevancy**, **Context Precision** (with reference).
- Judge LLM and embeddings: the same Ollama models via RAGAS' LangChain wrappers. Pin the `ragas` version in `pyproject.toml` and follow that version's API (it changed significantly across releases).
- Also report pipeline stats: verified rate, fallback rate, average iterations, average latency.
- Output: `data/eval/results-<YYYYMMDD-HHMM>.csv` + a printed summary.
- Note in reports: a local 8B judge is weaker than GPT-4-class judges; compare runs relatively, not as absolute truth.
- Useful ablation for the report: single-pass RAG (iteration 1, no verifier) vs full dual-agent loop.

---

## 14. Coding conventions

### Python (backend + AI)
- Python 3.12, full type hints, `ruff` for lint + format (line length 100).
- Pydantic models for every request/response body — no raw dicts across API boundaries.
- Config only via `pydantic-settings` (`config.py`); no `os.getenv` scattered around.
- Use `pathlib.Path` for all paths (Windows-safe). Never build paths with string `/` concatenation.
- Sanitize uploaded filenames; never trust client paths.
- `logging` module, not `print`. Log iteration count, verifier scores, and latency per chat turn. Never log passwords, tokens, or full JWTs.
- Async endpoints; run blocking work (parsing, Qdrant local calls) via `run_in_threadpool` / `asyncio.to_thread`. **Exception:** backend routes that use the database are plain `def` — SQLite has no async driver in this project, so FastAPI runs them in its threadpool. Routes doing real async I/O (e.g. AI-service HTTP calls) stay `async def`.
- Prompts are Markdown files in `ai_service/prompts/`, loaded at startup. Keep them versioned and reviewed like code.

### TypeScript (frontend)
- `strict: true`, no `any`. Functional components + hooks.
- Server state with TanStack Query; local UI state with `useState`/context.
- Tailwind for styling; no inline style objects except for dynamic values.

### Testing
- Backend: auth (register/login/role guard), upload validation, admin-only routes, session ownership.
- AI: parsers (one fixture per file type), chunker (sizes, overlap, parent links), verifier pass-rule logic, escalation routing (mock the LLM), safety_check terms, fallback after 3 fails.
- Mock Ollama in unit tests; mark tests that need a real Ollama with `@pytest.mark.ollama` (skipped by default).

### Git
- Branches: `feature/<short-name>`, `fix/<short-name>`.
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Never commit `data/`, `.env`, model files, or uploaded documents.

---

## 15. Build order (v1 milestones)

1. Repo skeleton, `.gitignore`, `.env.example` files, health endpoints for backend + AI service.
2. Backend auth: users table, register/login, JWT, admin seeding, role guards.
3. Admin document upload → AI ingestion (parse, chunk, embed, Qdrant + docstore) → status updates, delete.
4. AI pipeline: single-pass retrieve + generate (iteration 1 only) end to end.
5. Verifier + escalation loop + fallback (LangGraph).
6. Session memory + query condensing; safety_check + emergency notice.
7. Frontend: login/register, chat page, admin page.
8. RAGAS evaluation script + test set.
9. Hardening: error handling, tests, README.

Finish and test each milestone before starting the next. **§17 breaks these milestones
into commit-sized tasks with per-phase gates — work from there.**

---

## 16. Rules for Claude

- Do not change the tech stack, ports, folder layout, or service boundaries without asking.
- Do not add cloud AI APIs, telemetry, or new heavy dependencies without asking.
- Never weaken the medical safety rules in §7.
- Keep changes small and focused; one milestone/feature per change.
- Run the quality checks in §12 before calling a task done; report anything you could not run (e.g. tests needing Ollama).
- When a decision in this file changes, update this file in the same change.
- If something is ambiguous, ask rather than guess — especially for anything touching medical output.

---

## 17. Task breakdown (how v1 gets built)

The §15 milestones expanded into commit-sized tasks. Phases run in order; each phase has a
gate that must pass before the next phase starts. Task IDs are stable — use them in commit
messages and branch names (e.g. `feature/2.3-vector-store`).

### Phase 0 — Skeleton & health (M1)

| # | Task | Key files | Done when |
|---|---|---|---|
| ✅ 0.1 | `git init`, `.gitignore` (`data/`, `.env`, `.venv/`, `node_modules/`), `.gitattributes`, README stub | root | `git status` clean, no data/env tracked |
| ✅ 0.2 | Backend scaffold: `uv` project, app factory, `config.py` via pydantic-settings, ruff cfg (line 100), startup creates `data/*` dirs | `backend/pyproject.toml`, `app/main.py`, `app/config.py`, `.env.example` | `uv run uvicorn app.main:app` boots |
| ✅ 0.3 | AI service scaffold + `X-Internal-Key` dependency, binds `127.0.0.1` | `ai/ai_service/main.py`, `config.py`, `security.py` | wrong key → 401 |
| ✅ 0.4 | Health endpoints: AI checks Ollama reachable + `qwen3:8b`/`bge-m3` present + Qdrant opens; backend `/api/health` proxies it | `routers/health.py` (both services) | both return green with Ollama running |
| ✅ 0.5 | Frontend scaffold: Vite + React + TS strict, Tailwind, Router, TanStack Query, ESLint/Prettier/Vitest, `src/api/client.ts` | `frontend/` | `npm run dev`, `lint`, `typecheck`, `test` all pass |
| ✅ 0.6 | ~~Pull models, confirm `ollama ps` shows **100% GPU** at `num_ctx=8192`~~ | — | ✅ **done 2026-11-21** — both models pulled, both 100% GPU; findings in §2 and §6.8 |

**Gate: PASSED 2026-11-21.** All three services start; `/api/health` reports `ok` with both
models detected and Qdrant open. Verified: 401 without the internal key, 200 with it, nested
health through the backend, `degraded` (200, ~instant) with the AI service stopped, the `data/`
tree created with absolute paths logged, `data/`+`.env`+`node_modules` untracked, and the Vite
dev server serving the shell with Tailwind compiling. Checks: backend 14 tests, AI 12 tests,
frontend 3 tests, ruff + ruff format + ESLint + tsc + prettier all clean.

### Phase 1 — Auth & data model (M2)

- ✅ **1.1** `db.py`, SQLAlchemy 2.0 models `users` / `documents` / `chat_sessions`, Alembic init + initial migration.
- ✅ **1.2** `auth_service.py`: Argon2 hash/verify, JWT encode/decode (`JWT_EXPIRE_MINUTES`).
- ✅ **1.3** Schemas + `routers/auth.py`: `register` (always `role='user'`), `login`, `me`.
- ✅ **1.4** `deps.py`: `get_db`, `get_current_user`, `require_admin`.
- ✅ **1.5** Startup admin seeding from `ADMIN_EMAIL` / `ADMIN_PASSWORD` — idempotent, updates the password if the env value changed, never creates a second admin.
- ✅ **1.6** Tests: duplicate email, wrong password, missing/expired token, user hitting an admin route → 403, registration cannot set `role`.

**Gate: PASSED 2026-11-21.** `uv run pytest` green in `backend/` (58 tests), ruff + ruff format clean. Also verified end to end over real HTTP against a freshly migrated database: register 201 / duplicate 409 / short password 422 / `role` in body 422, login + `/me`, missing and mangled token 401, admin login, restart is a no-op, a changed `ADMIN_PASSWORD` updates the admin in place (old password rejected, still exactly one admin), and an unmigrated database refuses to boot.

### Phase 2 — Ingestion (M3)

Build the AI side first (testable standalone), then wire the backend.

- ✅ **2.1** `ingestion/parsers.py` — PyMuPDF with real page numbers; `python-docx`; DOCX/MD → heading-based section index, TXT → page 1. Near-zero text PDF → `"scanned or image-only PDF is not supported"`.
- ✅ **2.2** `ingestion/chunker.py` — parents ≈ 800 tokens on paragraph/heading boundaries; children ≈ 300 tokens / 50 overlap carrying `parent_id`; `len(text) / 4` estimator. Markdown tables split on row boundaries with the header repeated in every chunk.
- ✅ **2.3** `retrieval/vector_store.py` — one embedded `QdrantClient(path=...)` as a module singleton (single process only), collection `chunks`, **vector size 1024 + cosine**, payload per §6.1.5, delete-by-`doc_id` filter.
- ✅ **2.4** `retrieval/docstore.py` — SQLite parents keyed by `parent_id`.
- ✅ **2.5** `llm/ollama_client.py` — the **only** module that calls `ollama`: `embed()` + `chat()`, always `options={num_ctx, temperature}`, `think=False`, `keep_alive="30m"`, batched embedding, retry + timeout.
- ✅ **2.6** `ingestion/indexer.py` + `routers/ingest.py` — `POST /internal/ingest` → `{chunk_count}`, `DELETE /internal/documents/{doc_id}`; blocking work via `asyncio.to_thread`.
- ✅ **2.7** Backend `services/document_service.py` — extension + `MAX_UPLOAD_MB` check, SHA-256 dedupe (409), filename sanitization, save as `<doc_id>_<safe_name>`, row with `status="pending"`.
- ✅ **2.8** Backend `services/ai_client.py` — httpx + `X-Internal-Key`, long timeout for ingest, AI failures → 503 with a friendly message.
- ✅ **2.9** Backend `routers/admin.py` — upload (+ `BackgroundTasks` → ingest → `ready` / `failed` + `error`), list, delete (file + Qdrant points + docstore parents + row).
- ✅ **2.10** Tests: one fixture per file type, chunker invariants (sizes, overlap, parent links), upload validation matrix, admin-only guards.

**Gate:** uploading a real PDF via `/api/admin/documents` flips the status to `ready` with a sane `chunk_count`; delete removes its points and parents.

**Status 2026-11-21:** 2.1–2.10 built; AI 88 tests, backend 128 tests, ruff clean. Live smoke on a throwaway `DATA_DIR` with real `bge-m3`: a synthetic 4-page PDF → `ready` (12 chunks, 4 parents, pages 1–4), DOCX and MD → `ready`, an image-only PDF → `failed` with the exact scanned-PDF message, `.png` 415, duplicate 409, anonymous 401, delete removed points + parents + file. `bge-m3` at `num_ctx=8192` sits at 689 MB, 100% GPU (matches §2). **Gate: PASSED 2026-11-21** with a real reference document, `disease.md` (5 KB Markdown, 10 diseases in one table): upload → `ready`, `chunk_count` 6 (3 parents, ~1,243 tokens, matching the file size), a duplicate re-upload 409, sample queries embedded with live `bge-m3` retrieve the right chunks at scores 0.5–0.6, and delete → 204 with points (6), parents (3), file and row all gone. The WHO malaria PDF supplied first was made by "Microsoft: Print To PDF" with outlined glyphs and **no text layer**, so it was correctly rejected as scanned/image-only; the PDF path was then confirmed on a real text-layer PDF (a 1-page Google Docs résumé export: `ready`, 2 parents / 4 children, retrieval and delete verified), which also exposed 48 zero-width spaces per page that the parser now strips. Note for 4.8: a short factual query ("what is the CGPA") scored only 0.276 against its 300-token chunk, below `MIN_RETRIEVAL_SCORE` 0.35.

### Phase 3 — Single-pass pipeline (M4)

- ✅ **3.1** `prompts/generator.md` — grounded-only, `[n]` citation on every claim, explicit "the documents don't cover this" path, §7.3 dosage caution.
- ✅ **3.2** `retrieval/retriever.py` — iteration-1 Top-3 children, score gate on `MIN_RETRIEVAL_SCORE`, context assembly + `n → {filename, page}` source map.
- ✅ **3.3** `agents/generator.py` (temperature 0.2).
- ✅ **3.4** `graph/state.py` + `graph/pipeline.py` — LangGraph with `retrieve → generate → finalize` only.
- ✅ **3.5** AI `POST /internal/chat` + backend `POST /api/chat/message` (records `(session_id, user_id)` on the first message, rejects another user's session), returning `ChatResponse`.
- ✅ **3.6** End-to-end smoke test by HTTP against 1–2 real documents; sanity-check citation numbering.

**Gate: PASSED 2026-11-21.** A grounded, cited answer end to end with no frontend involved.

**Status 2026-11-21:** 3.1–3.6 built; AI 126 tests, backend 148 tests, ruff clean. Live smoke on a
throwaway `DATA_DIR` (ports 18000/18001, real `qwen3:8b` + `bge-m3`) with `disease.md` (8 chunks):
malaria, tuberculosis and common-cold questions returned `verified` answers that match the source
table row for row, each citing `[1]` = `disease.md` p. 1 (best scores 0.55 / 0.66 / 0.60; 9–16 s per turn
including the model swap). "What is the capital of France?" scored 0.318, under the gate, so it fell
back **without calling the LLM** (5.5 s); "How do I fix my car's brakes?" scored 0.409, passed the
gate, and the Generator's `NOT_IN_DOCUMENTS` sentinel sent it to the exact fallback text. A second user
reusing a session got 403, and a stopped AI service gave the friendly 503. `ollama ps`: `qwen3:8b`
6.2 GB, 100% GPU at `num_ctx=8192`.

### Phase 4 — Verifier & escalation (M5)

- ✅ **4.1** `prompts/verifier.md` + the §6.5 JSON schema as a Pydantic model.
- ✅ **4.2** `agents/verifier.py` — `format=<schema>`, temperature 0, audits the draft against the **retrieved context only**; parse failure → one retry → treat as `fail`.
- ✅ **4.3** Pass rule computed in code (`verdict` **and** both thresholds **and** empty `unsupported_claims`).
- ✅ **4.4** Escalation in `retriever.py` — level 2 (Top-8 → ≤ 4 parents), level 3 (Top-15 → ≤ 6 parents), parents de-duplicated and ordered by best child score; low score at iteration 1 → jump to 2; still low at the last iteration → fallback.
- ✅ **4.5** Graph `escalate` (iteration += 1, append `missing_info` to the retrieval query) + `fallback` nodes with conditional edges; hard cap at `MAX_ITERATIONS`.
- ✅ **4.6** Prompt-budget trimmer per §6.4 (history first, then lowest-scoring context, never the system prompt).
- ✅ **4.7** Tests with a mocked LLM: pass-rule truth table, 3 fails → exact fallback text, routing on low retrieval score, iteration never exceeds 3, malformed-JSON path.
- ✅ **4.8** Calibration pass — `FAITHFULNESS_THRESHOLD`, `RELEVANCE_THRESHOLD` and `MIN_RETRIEVAL_SCORE` defaults in §11 are starting guesses. Measure on ~10 real questions and record the chosen values here. A local 8B verifier tends to be lenient, so expect to adjust. Also decide here whether the §6.8 latency is acceptable or the `qwen3:4b` escape hatch is needed.

**Gate: PASSED 2026-11-21.** A question answerable only from a parent chunk reaches it by iteration 2; an off-corpus question returns the fallback and never a guess.

**Status 2026-11-21:** 4.1–4.8 built; AI 180 tests (+4 live behind `-m ollama`), backend 148 tests, ruff clean. Live calibration in-process on a throwaway `DATA_DIR` (real `qwen3:8b` + `bge-m3`; `disease.md` plus a synthetic migraine document, 12 questions):

| Kind | Result |
|---|---|
| 6 in-corpus + 2 short queries | all `verified` at iteration 1, faithfulness 1.00 / relevance 1.00; best scores 0.53–0.66; 11–19 s per turn |
| Parent-only ("migraine in children") | probe: answer text **absent** from level-1 context, present at level 2. Iteration 1 failed the audit (faithfulness 0.50), iteration 2 passed from the parent and cited it; 35 s |
| Off-corpus, France (0.32) | `low_score` → `not_covered` → `low_score` → exact fallback; 14 s |
| Off-corpus, car brakes (0.41, over the gate) | `not_covered` ×3 → exact fallback; 15 s |
| Dosage the sources do not state (paracetamol for a 5-year-old) | Verifier rejected the draft (faithfulness 0.00), then fallback; never a guess; 31 s |

**Calibration decisions.** The Verifier is bimodal: good drafts scored 1.00/1.00 and every bad one ≤ 0.50, so `FAITHFULNESS_THRESHOLD=0.85`, `RELEVANCE_THRESHOLD=0.75` and `MIN_RETRIEVAL_SCORE=0.35` sit well clear of both and are **unchanged**. `MIN_RETRIEVAL_SCORE` separates France (0.32) from the lowest in-corpus score (0.53); anything between is judged by the sentinel and the Verifier. The one question not re-measured is the short factual query from Phase 2 (0.276 on the résumé PDF).

**Latency decision.** One iteration takes 11–19 s and two take about 30–35 s including model swaps, so a full 3-iteration worst case is extrapolated at roughly 50–60 s (not measured directly): inside the §6.8 budget of 30–90 s and the 300 s backend timeout. The `qwen3:4b` escape hatch is **not needed**; §3 and §11 are unchanged. Largest prompt seen: 2,414 tokens, against the ~7,000 budget.

### Phase 5 — Session memory & safety (M6)

- **5.1** `memory/chat_store.py` — `data/chats/<session_id>.json`, atomic write (temp file then replace), turn shape per §6.7.
- **5.2** `agents/condenser.py` + `prompts/condense.md` (temperature 0, last `HISTORY_MAX_MESSAGES`; the first message passes through unchanged).
- **5.3** `agents/safety_terms.yaml` + `agents/safety.py` — deterministic regex/keyword match, sets `emergency=true`, pipeline still runs, response **prefixed** with the §7.2 notice.
- **5.4** Fallback text and disclaimer defined in exactly one place (config) and surfaced through the API.
- **5.5** Logging: per chat turn log iteration count, verifier scores and latency; never passwords, tokens or full JWTs.
- **5.6** Tests: every safety term matches, condenser with a mocked LLM ("what about for children?"), transcript round-trip.

### Phase 6 — Frontend (M7)

- **6.1** `src/api/` — typed `auth.ts`, `chat.ts`, `admin.ts`; bearer-token injection; 401 → logout.
- **6.2** `AuthContext` (token + role in `localStorage`) + role-based route guards.
- **6.3** `LoginPage`, `RegisterPage`.
- **6.4** `ChatPage` — `session_id = crypto.randomUUID()` in React state only, message list, "Thinking…" indicator, `react-markdown` (no raw HTML), citation list under each answer, red emergency banner when `emergency=true`, persistent disclaimer banner.
- **6.5** `AdminPage` — upload, document table with status (poll via TanStack Query while any row is `pending`), delete with confirmation, `error` text shown for `failed` rows.
- **6.6** `npm run lint ; npm run typecheck ; npm run test` green.

### Phase 7 — Evaluation (M8)

- **7.1** `eval/testset.jsonl` — 30–50 hand-written question / reference / source rows taken from the uploaded documents (team work, not model-generated).
- **7.2** `eval/run_ragas.py` — calls the LangGraph pipeline directly, pinned `ragas`, Ollama judge + embeddings, writes `data/eval/results-<ts>.csv`, prints Faithfulness / Response Relevancy / Context Precision **plus** verified rate, fallback rate, average iterations, average latency.
- **7.3** Ablation: iteration-1-without-verifier vs the full dual-agent loop — this is the headline result for the report.
- ⚠️ Qdrant local mode allows one process, so **the AI service must be stopped before running the eval**. `run_ragas.py` should fail fast with that exact explanation if it cannot acquire the storage lock.

### Phase 8 — Hardening (M9)

- **8.1** Error-handling sweep: Ollama down, model not pulled, empty knowledge base, oversized upload, AI-service timeout — each produces a specific, friendly message.
- **8.2** Fill test gaps; one script that runs all the §12 quality checks.
- **8.3** README (setup, run, troubleshoot) and both `.env.example` files in sync.
- **8.4** Performance check: `ollama ps` at 100% GPU, latency recorded per iteration, prompt budget verified under ~7,000 tokens.
- **8.5** Report assets: architecture diagram, metric tables, screenshots, ablation chart.

### Work split (two developers)

After Phase 1 the seams are clean enough to work in parallel:

- **Track A — AI service:** 2.1–2.6, all of Phases 3–5, Phase 7.
- **Track B — backend + frontend:** Phase 1, 2.7–2.10, the backend half of 3.5, all of Phase 6.
- Freeze the shared contracts in Phase 0 so both tracks can mock the other: `ChatResponse` and the `/internal/ingest` + `/internal/chat` bodies (§9). Do not change them without telling the other track.

### Settled decisions

- **Citation numbers are one per `(filename, page)`**: chunks from the same page share a number, assigned in best-score order, and `citations` lists only the numbers the answer uses. Markers that point at no source are stripped from the text.
- **Phase 3 guards fall back instead of guessing:** no chunk reaching `MIN_RETRIEVAL_SCORE` (the LLM is not called), the `NOT_IN_DOCUMENTS` sentinel, or no valid `[n]` all return the §7.4 fallback.
- **`VectorStore` serialises every Qdrant call with a lock, and chat turns run one at a time** (one model in VRAM, §6.8). The fallback and disclaimer text live as constants in `ai_service/config.py`.
- **Verifier replies that are not valid JSON for the schema** (including scores outside 0–1) are retried once, then count as a failed audit. An `OllamaError` is not a failed audit: it propagates to a 503.
- **`MAX_ITERATIONS` is validated to 1–3** at startup, because §6.3 defines exactly three levels.
- **Parent sources are cited at the parent's first page**, one `[n]` per `(filename, page_start)`; a parent missing from the docstore degrades to its child chunks with a warning.
- **`PipelineResult` also carries `iterations`, `attempts` and `sources`** for transcripts (5.1) and evaluation (7.x). The §9 `ChatResponse` body is unchanged.

- **Ingest is idempotent per `doc_id`** and embeds everything *before* writing, so a failed embed leaves the previous index intact; a write failure rolls both stores back for that id.
- **The AI service ingests only files inside `data/uploads/`** (anything else → 400), and serialises ingest/delete behind one lock.
- **A backend restart marks stale `pending` documents `failed`** ("ingestion was interrupted by a backend restart"), since the background task dies with the process; the admin deletes and re-uploads.
- **Deleting a `pending` document is refused (409)**, and delete asks the AI service first, so a row never outlives vectors it could no longer clean up.

- `CLAUDE.md` lives at the **repository root**.
- The AI service runs **without `--reload`** (see §12) because of the Qdrant storage lock.
- The `bge-m3` embedding dimension is **1024** (measured) — the Qdrant collection is created with it, and changing it later means a full re-index.
- **Models stay as specified in §3** (`qwen3:8b` + `bge-m3`) even though they cannot be co-resident in 8 GB VRAM. The ~7 s swap cost is accepted in exchange for answer quality and credible RAGAS numbers; §6.8 holds the rules for minimising swaps, and `qwen3:4b` is the documented escape hatch if 4.8 shows the latency is unworkable.
