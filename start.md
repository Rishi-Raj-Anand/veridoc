# Starting VeriDoc AI

Everything runs locally on Windows. There are four processes, and each one gets its own
PowerShell window:

| # | Service | Port | Folder |
|---|---|---|---|
| 1 | Ollama (`qwen3:8b`, `bge-m3`) | 11434 | — (Windows app) |
| 2 | AI service | 8001 | `ai/` |
| 3 | Backend | 8000 | `backend/` |
| 4 | Frontend | 5173 | `frontend/` |

Open the app at **http://localhost:5173**.

> Windows PowerShell 5.1 has no `&&`. Use `;` to chain commands.

---

## 1. First-time setup (once per machine)

**Prerequisites:** [Ollama for Windows](https://ollama.com/download), Node.js (with npm) and
`uv`. To install `uv`, run this and then open a **new** terminal, because the installer
changes `PATH`:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Pull the models.** Ollama must be running:

```powershell
ollama pull qwen3:8b
ollama pull bge-m3
```

**Create the `.env` files** from the examples. These files are git-ignored, so never commit
them:

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item ai\.env.example ai\.env
Copy-Item frontend\.env.example frontend\.env
```

Then edit them:
- `INTERNAL_API_KEY` must be the **same** secret in `backend\.env` and `ai\.env`.
- `JWT_SECRET` in `backend\.env` must be at least 32 characters.
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `backend\.env` are the admin login. The admin account
  is created, or its password updated, every time the backend starts.

**Install the dependencies and create the database.** `uv` downloads its own Python 3.12; ignore
the system Python:

```powershell
cd ai ; uv sync ; cd ..
cd backend ; uv sync ; uv run alembic upgrade head ; cd ..
cd frontend ; npm install ; cd ..
```

---

## 2. Start everything

Start the services in this order. Each `cd` is relative to the repository root, and every
service must be started **from its own folder**, because `DATA_DIR=../data` is relative.

**Window 1: Ollama.** It usually already runs as a tray app. Check:

```powershell
ollama list            # works = Ollama is running
# if it is not running:
ollama serve
```

**Window 2: AI service.** Never use `--reload` here: Qdrant's storage lock breaks on reload.

```powershell
cd ai
uv run uvicorn ai_service.main:app --host 127.0.0.1 --port 8001
```

**Window 3: Backend**

```powershell
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

**Window 4: Frontend**

```powershell
cd frontend
npm run dev
```

### One-shot: open all three windows at once

Run this from the **repository root**, with Ollama already running. Each service opens in its
own titled window:

```powershell
$root = (Get-Location).Path
Start-Process powershell -WorkingDirectory "$root\ai" -ArgumentList '-NoExit', '-Command', '$Host.UI.RawUI.WindowTitle = ''VeriDoc AI service :8001''; uv run uvicorn ai_service.main:app --host 127.0.0.1 --port 8001'
Start-Process powershell -WorkingDirectory "$root\backend" -ArgumentList '-NoExit', '-Command', '$Host.UI.RawUI.WindowTitle = ''VeriDoc backend :8000''; uv run uvicorn app.main:app --reload --port 8000'
Start-Process powershell -WorkingDirectory "$root\frontend" -ArgumentList '-NoExit', '-Command', '$Host.UI.RawUI.WindowTitle = ''VeriDoc frontend :5173''; npm run dev'
```

---

## 3. Check that it is up

```powershell
Invoke-RestMethod http://localhost:8000/api/health | ConvertTo-Json -Depth 5
```

`status` should be `ok`, with Ollama `ok`, both models present (`missing: []`) and Qdrant
`ok`. If the AI service is not up yet, the status is `degraded`.

The same check appears as the **System status** card on `/admin`.

```powershell
ollama ps    # while a chat is running: both models should show 100% GPU
```

**Logins:**
- **Admin:** the `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `backend\.env`. The admin lands on
  `/admin`, where documents are uploaded.
- **Users:** register at http://localhost:5173/register.

---

## 4. Stop and restart

- **Stop:** press `Ctrl+C` in each window.
- **After changing code:**
  - **AI service:** restart it by hand (`Ctrl+C`, then run the command again). It has no
    auto-reload.
  - **Backend:** reloads by itself (`--reload`).
  - **Frontend:** hot-reloads by itself.
- **After pulling new database migrations:** run `cd backend ; uv run alembic upgrade head`.

**Free a port** that is still held, for example after a window was closed without `Ctrl+C`:

```powershell
Get-NetTCPConnection -LocalPort 8001 -State Listen | Select-Object OwningProcess
Stop-Process -Id <OwningProcess> -Force
```

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Backend refuses to start: "database not initialised - run 'uv run alembic upgrade head' in backend/" | The database is not migrated. Run that command in `backend\`. |
| AI service: "could not open Qdrant local storage" | Another AI service (or an eval run) holds `data\vectors`. Only one process may open it: stop the other one. |
| Health shows `ollama: down` | Ollama is not running. Start the app or run `ollama serve`. |
| Health shows `missing: ["qwen3:8b"]` (or `bge-m3`) | The model is not pulled. Run `ollama pull <model>`. |
| Health shows `degraded` / chat says "Doctor AI is unavailable" | The AI service is not running, or `INTERNAL_API_KEY` differs between `backend\.env` and `ai\.env`. |
| A brand-new, empty `data` folder appears in the wrong place | A service was started from the wrong folder. Start each one from its own folder; both log the resolved data path at startup. |
| `npm : running scripts is disabled on this system` | PowerShell execution policy. Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `npm.cmd run dev`. |
| `Port 5173 is already in use` | An old Vite window is still running. Close it or free the port (section 4). |
| Upload `failed`: "Indexing did not finish within 60 minutes…" | The file is too big for the wait time (`bge-m3` embeds ~45 chunks/s; a 22 MB CSV takes ~10–12 min). Delete the row, then split the file or raise `INGEST_TIMEOUT_MINUTES` in `backend\.env`. |
| Delete says "could not remove this document from the search index" | The AI service was down or busy, so nothing was deleted on purpose (the index and the list stay in sync). Start the AI service and click Delete again. |
| An answer takes 30–90 s | Normal: the LLM and the embedding model swap in 8 GB of VRAM (CLAUDE.md §6.8). Small talk and refusals are near-instant. |

---

## 6. Project structure

Three services, each in its own folder with its own dependencies. The frontend talks **only** to
the backend, and the backend talks to the AI service over localhost with a shared key. Only the AI
service talks to Ollama (CLAUDE.md §4).

```text
veridoc/
├── README.md                    # project overview
├── CLAUDE.md                    # the spec: architecture, rules, API contracts, task plan (read first)
├── start.md                     # this file: setup, start, check, stop, troubleshoot
├── .gitignore                   # keeps data/, .env, .venv/, node_modules/ out of git
├── .gitattributes               # line-ending normalisation (* text=auto)
├── .claude/plans/               # per-phase plans with recorded test and live-gate results
│
├── data/                        # RUNTIME DATA, git-ignored, created on first start
│   ├── app.db                   #   backend SQLite: users, documents, chat_sessions
│   ├── uploads/                 #   original uploaded files, stored as <id>_<safe_name>
│   ├── vectors/                 #   Qdrant vector index (only the AI service may open it)
│   ├── docstore.db              #   parent chunks (bigger context used on retries)
│   ├── chats/                   #   one <session_id>.json transcript per chat session
│   ├── behaviour.json           #   admin-edited assistant behaviour (tone, scope, guardrails)
│   └── eval/                    #   RAGAS evaluation results (Phase 7)
│
├── frontend/                    # React + Vite + TypeScript UI           -> http://localhost:5173
│   ├── .env.example             #   VITE_API_URL (the backend address)
│   ├── index.html               #   HTML shell and favicon link
│   ├── package.json             #   npm scripts: dev, build, lint, typecheck, test
│   ├── vite.config.ts           #   dev server on 5173, Tailwind plugin, Vitest setup
│   ├── tsconfig.json            #   strict TypeScript (one config file, on purpose)
│   ├── eslint.config.js         #   lint rules (no `any`)
│   ├── public/favicon.svg       #   the shield logo used as the tab icon
│   └── src/
│       ├── main.tsx             #   entry: QueryClient, AuthProvider, router
│       ├── App.tsx              #   all routes and their role guards
│       ├── index.css            #   Tailwind import, plus the 404 heartbeat animation
│       ├── constants.ts         #   accepted file types, length limits, disclaimer text
│       ├── api/                 #   EVERY backend call lives here, fully typed
│       │   ├── client.ts        #     fetch wrapper: bearer token, sign out on 401, error text
│       │   ├── auth.ts          #     register / login / me
│       │   ├── chat.ts          #     send a message -> ChatResponse
│       │   ├── admin.ts         #     list / upload / delete documents
│       │   ├── behaviour.ts     #     get / save assistant behaviour
│       │   └── health.ts        #     service health
│       ├── context/             #   AuthContext (who is signed in) and its localStorage copy
│       ├── hooks/useAuth.ts     #   access to the auth context
│       ├── pages/
│       │   ├── HomePage.tsx            # /home      landing page with "Try out AI Doctor"
│       │   ├── LoginPage.tsx           # /login
│       │   ├── RegisterPage.tsx        # /register  (signs in automatically afterwards)
│       │   ├── ChatPage.tsx            # /chat      Doctor AI conversation
│       │   ├── AdminPage.tsx           # /admin     system status, upload, document list
│       │   ├── AdminBehaviourPage.tsx  # /admin/behaviour  tone, scope, guardrails, locked rules
│       │   └── NotFoundPage.tsx        # any unknown URL: the funny 404 "patient file"
│       ├── components/
│       │   ├── AppLayout.tsx    #     header and navigation around the signed-in pages
│       │   ├── RequireAuth.tsx  #     route guard: signed out -> /login, wrong role -> /chat
│       │   ├── RedirectIfAuthed.tsx  # keeps signed-in users off /login and /register
│       │   ├── AuthCard.tsx     #     shared login/register form
│       │   ├── HealthBanner.tsx #     "System status" card on the admin page
│       │   ├── Logo.tsx         #     inline SVG logo
│       │   ├── routes.ts        #     where each role lands after login
│       │   ├── chat/            #     message bubbles, Markdown answer, citations,
│       │   │                    #     emergency and disclaimer banners, input box, "Thinking..."
│       │   └── admin/           #     admin tabs, document table, status badges
│       ├── test/                #   test setup and helpers (fake backend, app renderer)
│       └── **/*.test.ts(x)      #   Vitest tests, next to the code they cover
│
├── backend/                     # FastAPI: users, auth, uploads, chat gateway  -> :8000
│   ├── .env.example             #   JWT secret, admin login, AI service URL + key, limits
│   ├── pyproject.toml / uv.lock #   Python 3.12 dependencies (managed by uv)
│   ├── alembic.ini, alembic/    #   database migrations; versions/ holds the schema history
│   ├── app/
│   │   ├── main.py              #   app factory, CORS, startup (create data/, seed the admin)
│   │   ├── config.py            #   all settings from .env (the only place that reads them)
│   │   ├── db.py                #   SQLAlchemy engine and sessions (foreign keys enforced)
│   │   ├── deps.py              #   get_db, current user, require_admin
│   │   ├── models/              #   tables: user.py, document.py, chat_session.py
│   │   ├── schemas/             #   request and response shapes (Pydantic); types.py makes times UTC
│   │   ├── routers/
│   │   │   ├── auth.py          #     /api/auth/register, /login, /me
│   │   │   ├── chat.py          #     /api/chat/message (checks session ownership)
│   │   │   ├── admin.py         #     /api/admin/documents: upload, list, delete
│   │   │   ├── admin_behaviour.py #   /api/admin/behaviour: get, save
│   │   │   └── health.py        #     /api/health (includes the AI service's health)
│   │   └── services/
│   │       ├── auth_service.py  #     Argon2 passwords, JWTs, admin seeding
│   │       ├── document_service.py  # upload checks (type, size, duplicates), file storage
│   │       ├── chat_service.py  #     records which user owns each chat session
│   │       └── ai_client.py     #     HTTP client for the AI service (timeouts, friendly errors)
│   └── tests/                   #   pytest: auth, uploads, admin guards, chat, AI client, migrations
│
└── ai/                          # FastAPI + LangGraph: all AI work           -> 127.0.0.1:8001
    ├── .env.example             #   Ollama host, model names, thresholds, Top-K, chunk sizes
    ├── pyproject.toml / uv.lock #   Python 3.12 dependencies (ragas in an optional "eval" group)
    ├── ai_service/
    │   ├── main.py              #   startup: opens Qdrant, loads prompts, builds the pipeline
    │   ├── config.py            #   settings, plus the fixed safety texts (fallback, emergency
    │   │                        #   notice, disclaimer) and the locked safety rules
    │   ├── security.py          #   X-Internal-Key check on every route
    │   ├── deps.py              #   gives routes the objects built at startup
    │   ├── contracts.py         #   shared Intent and ChatStatus values
    │   ├── behaviour.py         #   admin-editable behaviour, saved in data/behaviour.json
    │   ├── storage.py           #   atomic file writes (temp file + replace)
    │   ├── routers/             #   /internal/ingest + documents, /chat, /behaviour, /health
    │   ├── ingestion/
    │   │   ├── parsers.py       #     PDF / DOCX / TXT / MD / CSV -> text with page or section numbers
    │   │   ├── csv_to_md.py     #     CSV -> Markdown, one section per entry (also a CLI)
    │   │   ├── chunker.py       #     parent (~800 tokens) and child (~300 tokens) chunks
    │   │   └── indexer.py       #     embeds children, writes Qdrant and the parent store
    │   ├── retrieval/
    │   │   ├── vector_store.py  #     Qdrant collection "chunks" (1024-dim, cosine)
    │   │   ├── docstore.py      #     SQLite parent chunks
    │   │   └── retriever.py     #     Top-3 / Top-8 / Top-15 search, score gate, numbered sources
    │   ├── agents/
    │   │   ├── safety.py + safety_terms.yaml  # red-flag emergency detection (rules, no LLM)
    │   │   ├── classifier.py    #     medical / small talk / off-topic routing
    │   │   ├── smalltalk.py     #     short friendly replies with no medical content
    │   │   ├── condenser.py     #     rewrites a follow-up into a standalone question
    │   │   ├── generator.py     #     drafts the cited answer from the sources only
    │   │   ├── verifier.py      #     audits the draft (faithfulness, relevance -> pass/fail)
    │   │   ├── citations.py     #     cleans [n] markers, detects "not in documents"
    │   │   ├── budget.py        #     keeps prompts inside the 8,192-token window
    │   │   └── admin_text.py    #     appends the admin's tone and guardrails to the prompts
    │   ├── prompts/             #   the system prompts as Markdown: generator, verifier,
    │   │                        #   condense, classify, smalltalk
    │   ├── graph/
    │   │   ├── state.py         #     what one chat turn carries through the graph
    │   │   └── pipeline.py      #     the LangGraph: safety -> classify -> condense ->
    │   │                        #     retrieve -> generate -> verify -> escalate / finalize / fallback
    │   ├── llm/ollama_client.py #   the ONLY module that calls Ollama (num_ctx, no thinking, retries)
    │   └── memory/chat_store.py #   chat transcripts in data/chats/
    └── tests/                   #   pytest with a fake LLM; tests marked "ollama" need the real one
```

---

## Extras

**Preview how a CSV will be indexed.** You can upload a `.csv` directly; this only shows what
the conversion produces:

```powershell
cd ai
uv run python -m ai_service.ingestion.csv_to_md path\to\file.csv --underscores-to-spaces
```

**Quality checks**, the same ones run before every commit:

```powershell
cd ai ; uv run ruff check . ; uv run ruff format --check . ; uv run pytest ; cd ..
cd backend ; uv run ruff check . ; uv run ruff format --check . ; uv run pytest ; cd ..
cd frontend ; npm run lint ; npm run typecheck ; npm run test ; cd ..
```

Full details live in [CLAUDE.md](CLAUDE.md) §12.
