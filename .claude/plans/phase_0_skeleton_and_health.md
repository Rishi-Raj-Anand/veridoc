# Phase 0 — Skeleton & health (Milestone 1)

| | |
|---|---|
| **Status** | ✅ **Complete — gate passed 2026-11-21** |
| **Branch** | `feature/phase-0-skeleton` |
| **Commits** | `c028a31` · `62d1976` · `25764a0` · `bd41c13` · `89dfa07` · `0c06c5a` |
| **Tasks** | 0.1 – 0.6, all done |
| **Spec** | `CLAUDE.md` §17 holds the live task list; this file is the Phase 0 record |

---

## Context

`veridoc/` contained exactly one file: `CLAUDE.md`. Phase 0 turned that spec into a running
three-service skeleton — backend, AI service, frontend — with nothing but health endpoints
behind it.

Its purpose was to prove the §4 service boundaries and the local Ollama + Qdrant stack work on
this machine *before* any feature code depends on them, and to freeze the shared API contracts
so the two tracks in §17 can work in parallel afterwards.

## Reconnaissance (2026-11-21)

| Checked | Result | Consequence |
|---|---|---|
| `uv` | not installed | installed during the phase (0.13.9, to `~/.local/bin`) |
| System Python | 3.14.6, spec wants 3.12 | uv provisions its own **3.12.16**; system Python untouched |
| Node / npm | v24.18.0 / 11.16.0 | used as-is |
| Git | 2.56.1, user configured | `git init -b main` (2.56 still defaults to `master`) |
| `typescript-eslint@8.73.0` | peers `typescript >=4.8.4 <6.1.0` | **TS pinned to 6.0.3**, not latest 7.0.2 |
| Tailwind | 4.3.5, CSS-first | **no `tailwind.config.js`**; `@tailwindcss/vite` plugin |
| `react-router` | v8.5.2, peers React >=19.2.7 | use `react-router`, not deprecated `react-router-dom` |
| `bge-m3` | 1024-dim, 689 MB resident | Qdrant collection vector size, pinned for task 2.3 |
| `qwen3:8b` | 6.2 GB resident at `num_ctx=8192` | cannot co-reside with `bge-m3` in 8 GB — see §6.8 |

## Task 0.1 — Repo skeleton ✅

`git init -b main`, `.gitignore`, `.gitattributes` (`* text=auto`), README stub.

`.gitignore` covers `data/`, `.env` and `.env.*` with `!.env.example`, `.venv/`,
`__pycache__/`, `.ruff_cache/`, `.pytest_cache/`, `node_modules/`, `dist/`, `.vite/`.

**Verified:** `.env` is ignored at every depth (`backend/.env`, `ai/.env`, `frontend/.env`)
while `.env.example` stays visible; `data/` ignored. README is a stub — task 8.3 fills it out.

## Task 0.2 — Backend scaffold ✅

```
backend/
├── pyproject.toml  .python-version (3.12)  .env.example  uv.lock
├── app/
│   ├── main.py  config.py
│   ├── routers/health.py
│   ├── schemas/health.py
│   └── services/ai_client.py
└── tests/  conftest.py  test_config.py  test_health.py
```

- **`config.py`** is the only reader of configuration (§14). `DATA_DIR` resolves to an absolute
  path at startup, and `CORS_ORIGINS` accepts a bare comma-separated string via
  `Annotated[list[str], NoDecode]` plus a validator — pydantic-settings would otherwise try to
  JSON-decode it and fail.
- **`services/ai_client.py`** speaks HTTP to the AI service with `X-Internal-Key`, validates the
  response into a Pydantic model rather than passing a dict through, and maps 401 to an explicit
  "wrong INTERNAL_API_KEY" hint.
- **`/api/health`** returns **200 with `status="degraded"`** when the AI service is down, not
  503. A health check that fails along with its dependency cannot report *which* dependency
  failed; the §9 mapping of AI failures to 503 applies to chat requests.
- Pins: `fastapi` 0.165.0, `pydantic` 2.14.4, **`sqlalchemy` 2.1.9** (current release of the 2.x
  line the spec calls "2.0"), `alembic` 1.20.1, `pyjwt` 2.15.1, `argon2-cffi` 25.1.0.

Alembic is a dependency but is **not initialised** — that is task 1.1, so there is no database
in Phase 0.

**Verified:** ruff clean, format clean, **14 tests pass**, Python 3.12.16.

## Task 0.3 — AI service scaffold ✅

```
ai/
├── pyproject.toml  .python-version (3.12)  .env.example  uv.lock
├── ai_service/
│   ├── main.py  config.py  security.py
│   └── routers/health.py
└── tests/  conftest.py  helpers.py  test_security.py  test_health.py
```

- **`security.py`** enforces `X-Internal-Key` with `secrets.compare_digest` on **every**
  `/internal/*` route, health included, so the auth path is exercised from Phase 0. The header is
  declared optional so a missing key yields **401, not FastAPI's 422**.
- **`main.py`** lifespan opens the single embedded `QdrantClient(path=...)` this process is
  allowed to hold (§4) via `asyncio.to_thread`, and on failure raises with an explicit
  single-process / no-`--reload` hint.
- **`/internal/health`** reports Ollama, models and Qdrant as **three independent checks** so a
  failure names the broken dependency. Model matching normalises tags: `EMBED_MODEL=bge-m3` is
  listed by Ollama as `bge-m3:latest`, so raw string comparison would report a pulled model as
  missing.
- **`ragas` 0.4.4 lives in an optional `eval` dependency group** (`uv sync --group eval`), not the
  default deps — it pulls langchain and datasets and is only needed for task 7.2.

**Verified:** ruff clean, format clean, **12 tests pass**. No test needs a live Ollama; the
`ollama` pytest marker is registered and deselected by default.

## Task 0.4 — Health contract ✅

Frozen so the §17 parallel tracks can mock against it:

```jsonc
// GET /api/health                         // GET /internal/health
{ "status": "ok|degraded",                 { "status": "ok|degraded",
  "backend": "ok",                           "ollama": {"ok": true, "host": "..."},
  "ai_service": { /* body at right */ },     "models": {"llm": true, "embed": true,
  "error": null }                                       "missing": []},
                                             "qdrant": {"ok": true, "path": "..."} }
```

The backend mirrors the AI body as Pydantic models with `extra="allow"`, so the AI service can
add fields without breaking the backend.

## Task 0.5 — Frontend scaffold ✅

Vite 8.3.8 + React 19.3.1 + Tailwind 4.3.5 + TanStack Query 5.108.2, 12 source files, with
placeholder pages for `/login`, `/register`, `/chat`, `/admin` and a live health strip proving
the whole chain from the browser.

- **TypeScript pinned to 6.0.3**, not 7.0.2: on TS 7, `typescript-eslint` disengages and *every*
  TypeScript lint rule silently switches off, including the §14 "no `any`" rule. Revisit when
  that peer range widens.
- **Tailwind v4 is CSS-first**: `@tailwindcss/vite` plugin plus one `@import "tailwindcss";` in
  `index.css`. There is no `tailwind.config.js` by design.
- **`src/api/client.ts`** is the single fetch wrapper every later API module builds on: typed
  responses, `ApiError` carrying the §9 `detail`, and a token-provider seam for task 6.1.
  Components never call `fetch` (§10).

**Verified:** `lint`, `typecheck`, **3 tests**, production build and `prettier --check` all pass.
Confirmed the lint chain is not silently empty: **16 `react-hooks/*` rules active** and
`no-explicit-any` at error level.

## Task 0.6 — Models ✅

`qwen3:8b` and `bge-m3` pulled, both at **100% GPU**. Measurements recorded in `CLAUDE.md` §2.

---

## Deviations from the original plan

| Planned | Actual | Why |
|---|---|---|
| `npm create vite@latest` | frontend written by hand | the template pulls TS 7.0.2 and a three-file project-reference tsconfig; writing it directly pinned the surveyed versions and avoided interactive prompts |
| Vite's 3-file tsconfig | single `tsconfig.json` | `tsc --noEmit` (what `npm run typecheck` runs) conflicts with the `composite: true` that project references require |
| — | 4 ruff findings fixed | two dead `# noqa: BLE001` directives (BLE not in the select list), `None` not last in a union (RUF036), mutable class attribute in a test (RUF012) |
| — | `asyncio_default_fixture_loop_scope` set | silences a pytest-asyncio deprecation warning in both services |

## Gate result — all seven checks passed

| # | Check | Result |
|---|---|---|
| 1 | AI health without the key | **401** `missing or invalid internal API key` |
| 2 | AI health with the key | **200** `ok` — ollama up, `llm ✓`, `embed ✓`, qdrant open |
| 3 | Backend health | **200** `ok`, AI block nested inside |
| 4 | Backend with the AI service stopped | **200** `degraded`, names the dependency, ~instant |
| 5 | `data/` tree | `uploads`, `vectors`, `chats`, `eval` created; both services log the same absolute path |
| 6 | Git hygiene | `data/`, `.env`, `node_modules` all untracked |
| 7 | Frontend | shell served, Tailwind compiling (8.5 KB CSS), React refresh wired |

**Tests: 29 passing** (backend 14, AI 12, frontend 3). ruff, ruff format, ESLint, tsc and
prettier all clean.

Also verified after the gate: writing the real `.env` files and restarting both services brought
health back to `ok`, and the old `change-me-shared-secret` default is now **rejected with 401** —
proving the generated key is genuinely in force rather than silently falling back to defaults.

## Findings folded back into CLAUDE.md

- **§2 / §6.8** — `qwen3:8b` (6.2 GB) and `bge-m3` (689 MB) cannot co-reside in 8 GB VRAM;
  Ollama evicts one per switch at **~7 s**. Accepted rather than worked around. Rules added for
  minimising switches: keep `generate` and `verify` adjacent, never interleave an embed between
  them, batch all embedding during ingest, budget 30–90 s per answer. `qwen3:4b` is the
  documented escape hatch if task 4.8 shows the latency is unworkable.
- **§6.8** — Qdrant's `data/vectors/.lock` is **advisory and does not go stale**: the AI service
  was hard-killed and restarted cleanly. A crash needs no manual lock cleanup; the single-writer
  rule still applies to *concurrent* processes, which matters for task 7.2.
- **§6.1 / task 2.3** — `bge-m3` embedding dimension is **1024**, cosine distance.
- **§3** — new "Pinned versions" table for pins that differ from the prose.
- **§5** — `.python-version` files and `frontend/.env.example` added to the layout.
- **§12** — AI service runs **without `--reload`**; uv provisions its own 3.12; `alembic upgrade
  head` only applies from Phase 1; `DATA_DIR` is CWD-relative so start each service from its own
  folder.

## Operational notes

- **Start order / commands** — see `CLAUDE.md` §12. The AI service must run **without
  `--reload`** (Qdrant storage lock).
- **`.env` changes need a manual restart** of both Python services: `uvicorn --reload` watches
  `.py`, not `.env`. They must restart *together*, or the `INTERNAL_API_KEY` mismatch shows up as
  `degraded` with a 401. Vite does auto-restart on `.env` change.
- **Secrets** — `JWT_SECRET` (64 chars) and `INTERNAL_API_KEY` (43 chars, identical in
  `backend/.env` and `ai/.env`) are generated; `ADMIN_PASSWORD` is set but **weak (`12345`)** and
  must change before any non-local exposure. The admin account is not seeded yet — that is task
  1.5.

## What does not exist yet

No database (`data/app.db` absent, Alembic uninitialised), no users, no auth, no login. No
document upload, parsing, chunking, embeddings, retrieval or agents. No LangGraph pipeline. The
four frontend pages are stubs. The plumbing is proven; none of the product is built.

## Next — Phase 1: Auth & data model (Milestone 2)

`1.1` models + Alembic migration → `1.2` Argon2 + JWT → `1.3` register/login/me → `1.4` role
guards → `1.5` admin seeding → `1.6` tests. Gate: `uv run pytest` green in `backend/`.

Backend-only, so once it lands the AI track (Phases 2–3) and the frontend track (Phase 6) can
proceed in parallel per the §17 split.
