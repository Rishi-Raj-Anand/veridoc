# Phase 2 — Ingestion (Milestone 3)

| | |
|---|---|
| **Status** | Planned |
| **Branch** | `feature/phase-2-ingestion` off `main` (`42ee084`) |
| **Tasks** | 2.1 – 2.10 (AI side first, then backend) |
| **Gate** | A real PDF uploaded via `/api/admin/documents` flips to `ready` with a sane `chunk_count`; delete removes its Qdrant points and docstore parents |
| **Touches** | `ai/` and `backend/` — no frontend, no LLM generation |
| **Record** | Copied to `.claude/plans/phase_2_ingestion.md` in the repo at task 2.0, results filled in as tasks land (Phase 0/1 convention) |

---

## Context

Phases 0–1 left a working auth backend and an AI service that only answers `/internal/health`.
Nothing can enter the knowledge base yet: no parsers, no chunker, no Qdrant collection, no
docstore, no Ollama wrapper, and no admin upload route. Phase 3's retriever needs indexed
child chunks and parent chunks to exist, so Phase 2 builds the whole ingest path:
admin upload → backend validation + storage → AI parse → parent/child chunk → `bge-m3` embed →
Qdrant + docstore → status `ready`, plus delete.

What exists and gets reused:
- `ai_service/main.py` — `lifespan` already opens the one embedded `QdrantClient` (`open_qdrant`, with `QDRANT_LOCK_HINT`) and stores it on `app.state.qdrant`.
- `ai_service/config.py` — `Settings` with `docstore_path` already defined; `security.require_internal_key`.
- `ai_service/routers/health.py` — `normalise_model_name`, `listed_model_names`; currently calls `ollama.Client` directly (must move behind the wrapper, §6.6).
- `backend/app/services/ai_client.py` — `AiServiceError`, `internal_headers`, `_url`, `fetch_health` pattern.
- `backend/app/deps.py` — `DbSession`, `AdminUser`; `models/document.py` already has every §8 column (**no migration needed**).
- Test patterns: `ai/tests/helpers.py` (`build_app`, `internal_client`, fakes), `backend/tests/helpers.py` (`build_client`, `token_for`, `bearer`), both autouse `DATA_DIR` fixtures.
- Pinned deps already include `pymupdf==2.1`, `python-docx==1.2.0`, `qdrant-client==1.20.0`, `ollama==0.6.3`, `python-multipart` — **no new dependencies**.

## Decisions

| Question | Decision |
|---|---|
| Non-PDF "page" | **Section index** (user choice). DOCX `Heading 1`/`Heading 2` styles and Markdown `#`/`##` lines start a new section number; text before the first heading is section 1; no headings → everything is page 1. TXT: no headings → page 1. |
| Gate PDF | **User supplies a real medical PDF path** at gate time. |
| Qdrant client ownership | Stays opened once in `lifespan`, wrapped in a `VectorStore` on `app.state.vector_store` (process singleton; the eval script in Phase 7 builds its own). |
| Docstore engine | stdlib `sqlite3` (no SQLAlchemy in the AI service), one short-lived connection per operation so `asyncio.to_thread` workers never share a connection. |
| Ingest atomicity | Delete existing data for `doc_id` → parse → chunk → **embed everything before any write** → write parents → upsert points; on a write failure, roll both back for that `doc_id`. Re-ingesting the same id is idempotent. |
| Concurrency | One `asyncio.Lock` in the ingest router serialises ingest and delete — protects Qdrant local mode and avoids VRAM thrash. |
| File path trust | AI service resolves `file_path` and rejects it (400) unless it is inside `DATA_DIR/uploads`. |
| Chunk sizes | `Settings` fields `PARENT_CHUNK_TOKENS=800`, `CHILD_CHUNK_TOKENS=300`, `CHILD_CHUNK_OVERLAP_TOKENS=50`, `EMBED_DIM=1024`; added to `ai/.env.example` with a "changing these requires re-ingesting every document" note. Timeouts/batch sizes stay module constants (precedent: `HEALTH_TIMEOUT_SECONDS`). |
| Upload status codes | wrong extension or content mismatch **415**, too large **413**, empty **422**, duplicate SHA-256 **409**, upload response **201** with `status="pending"`. |
| Delete while `pending` | **409** "document is still being processed" — avoids racing the background ingest. |
| Backend restart mid-ingest | On startup, any `pending` row becomes `failed` with "ingestion interrupted by a backend restart — delete and re-upload". |
| Stored path | DB stores `stored_path` relative to `DATA_DIR` (`uploads/<id>_<safe>`); the absolute path is sent to the AI service. `filename` keeps the original basename for display/citations. |

---

## Task 2.0 — Housekeeping
Branch `feature/phase-2-ingestion`; copy this plan to `.claude/plans/phase_2_ingestion.md`.

## Track A — AI service

### 2.1 `ai_service/ingestion/parsers.py`
- `@dataclass(frozen=True) PageText(page: int, text: str)`; `parse_document(path) -> list[PageText]` dispatching on lower-cased suffix; `class ParseError(ValueError)` with user-safe messages.
- **PDF** (PyMuPDF): `page.get_text("text", sort=True)` per page, 1-based page numbers; encrypted/corrupt → `ParseError`; average < `MIN_CHARS_PER_PDF_PAGE` (≈ 50 non-whitespace chars) → `"scanned or image-only PDF is not supported"`.
- **DOCX**: walk paragraphs (and table cell text), headings bump the section counter.
- **TXT/MD**: decode `utf-8-sig`, fall back to `cp1252`; MD `#`/`##` bump sections.
- Normalise whitespace (collapse runs, keep blank-line paragraph breaks); drop empty pages; no text at all → `ParseError("document contains no extractable text")`. Unsupported suffix → `ParseError`.

### 2.2 `ai_service/ingestion/chunker.py`
- `estimate_tokens(text) = ceil(len(text) / 4)`.
- Input `list[PageText]` → `ParentChunk(parent_id, index, page_start, page_end, text)` and `ChildChunk(parent_id, chunk_index, page, text)`.
- **Parents**: split into blocks on blank lines / heading lines; greedily pack blocks up to `PARENT_CHUNK_TOKENS`; a heading starts a new parent if the current one is ≥ half full; an oversize block is split on sentence boundaries, then hard at whitespace. Parents may span pages (track a char-offset → page map); a tiny trailing parent merges into the previous one.
- **Children**: sliding window inside each parent, `CHILD_CHUNK_TOKENS` with `CHILD_CHUNK_OVERLAP_TOKENS` overlap, ends snapped to whitespace; children never cross parents; `page` = page at the child's start offset; `chunk_index` global per document.
- `parent_id = f"{doc_id}:p{index:05d}"` (deterministic).

### 2.3 `ai_service/retrieval/vector_store.py`
- `class VectorStore(client, settings)`: `ensure_collection()` creates `chunks` (size `EMBED_DIM`, `Distance.COSINE`) if absent, and **raises** if it exists with a different size (names the re-index fix).
- `upsert_children(doc_id, filename, children, vectors)` in batches of 256, `wait=True`; point id = `uuid5(NAMESPACE, f"{doc_id}:{chunk_index}")`; payload exactly §6.1.5 `{doc_id, filename, page, parent_id, chunk_index, text}`.
- `delete_document(doc_id)` via `FilterSelector` on `doc_id`; `count_document(doc_id)` (tests + gate). Search is Phase 3.2.
- `main.lifespan`: wrap the opened client, call `ensure_collection()`, set `app.state.vector_store`; `get_vector_store(request)` dependency. Health uses it (`FakeQdrant` in tests adapts).

### 2.4 `ai_service/retrieval/docstore.py`
- `class Docstore(path)`; schema created on init: `parents(parent_id TEXT PK, doc_id INT, parent_index INT, filename, page_start, page_end, text)` + index on `doc_id`; WAL mode.
- `replace_document(doc_id, parents)` (delete + insert in one transaction), `get_parents(ids) -> list` preserving request order (Phase 4 needs it), `delete_document(doc_id) -> int`, `count_document(doc_id)`.
- Opened in `lifespan` as `app.state.docstore`.

### 2.5 `ai_service/llm/ollama_client.py` — the only importer of `ollama`
- `class OllamaClient(settings)`, one `ollama.Client(host, timeout=...)`.
- `embed(texts) -> list[list[float]]`: batches of `EMBED_BATCH_SIZE` (32), `options={"num_ctx": NUM_CTX}`, `keep_alive="30m"`, validates every vector length == `EMBED_DIM`.
- `chat(messages, *, temperature, format=None) -> str`: always `options={"num_ctx", "temperature"}`, `think=False`, `keep_alive="30m"` (built now, first used in Phase 3).
- `list_models() -> set[str]` — health moves onto this; `normalise_model_name`/`listed_model_names` move here and health imports them.
- Retry: 2 retries with 1 s / 3 s backoff on connection errors, timeouts and 5xx; **no retry** on 404 → `OllamaError("model 'bge-m3' is not pulled - run: ollama pull bge-m3")`. Everything surfaces as `OllamaError` with a friendly message.

### 2.6 `ingestion/indexer.py` + `routers/ingest.py`
- `index_document(doc_id, file_path, filename, *, store, docstore, llm, settings) -> IngestResult` — the sequence in Decisions, blocking, logs parent/child counts and timings (parse / chunk / embed / write).
- `routers/ingest.py` (`/internal`, `require_internal_key`, `async def`, work via `asyncio.to_thread`, serialised by a module `asyncio.Lock`):
  - `POST /internal/ingest` `{doc_id, file_path, filename}` → `{chunk_count}` (children count). Path outside uploads → 400; missing file → 404; `ParseError` → 422 with its message; `OllamaError` → 503; else 500 logged.
  - `DELETE /internal/documents/{doc_id}` → 200 `{deleted_points, deleted_parents}` (idempotent; extra keys allowed by the backend).
- `config.py` gains `uploads_dir` property; include router in `create_app`.

## Track B — Backend

### 2.7 `app/services/document_service.py`
- `ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}`; `sanitize_filename(name)`: basename after splitting on `/` and `\`, NFKC, `[^A-Za-z0-9._-]` → `_`, collapse, strip leading dots, cap ~100 chars keeping the extension, fallback `document`.
- `save_upload(db, settings, upload, admin) -> Document`: check extension → stream to a temp file in `uploads/` in 1 MiB chunks, hashing SHA-256 and aborting past `max_upload_bytes` (413) → empty (422) → light content check (PDF starts `%PDF-`, DOCX starts `PK\x03\x04`, TXT/MD has no NUL bytes; mismatch 415) → duplicate sha (409, temp removed) → insert row (`pending`), flush for the id, rename temp to `<id>_<safe>`, set `stored_path`, commit. Any failure removes the temp file; an `IntegrityError` on sha (race) → 409.
- `mark_interrupted_ingests(db)` for startup; `delete_document_files(settings, doc)` (`missing_ok`).
- Custom exceptions mapped to `HTTPException` in the router, so the service stays HTTP-free.

### 2.8 `app/services/ai_client.py`
- `INGEST_TIMEOUT_SECONDS = 600`, `DELETE_TIMEOUT_SECONDS = 30`.
- `ingest_document(settings, doc_id, file_path, filename) -> IngestResult` and `delete_document_index(settings, doc_id)`; share one `_post/_delete` helper with `fetch_health`'s error mapping. A 4xx/5xx carrying a `detail` raises `AiServiceError` whose message includes that detail (e.g. "scanned or image-only PDF is not supported"); unreachable → "AI service unavailable".
- `schemas/document.py`: `IngestRequest`, `IngestResult`, `DocumentResponse` (`id, filename, size_bytes, status, chunk_count, error, created_at`; no `stored_path`/`sha256`).

### 2.9 `app/routers/admin.py` (`prefix="/admin/documents"`, every route takes `AdminUser`)
- `POST` (`def`, multipart `file: UploadFile`) → 201 `DocumentResponse`; schedules `run_ingest(doc_id)` via `BackgroundTasks`.
- `run_ingest` (`async def`): calls `ingest_document`, then a fresh session sets `ready` + `chunk_count` or `failed` + `error` (truncated to 2000); skips if the row vanished; never raises.
- `GET` → list newest first. `DELETE /{id}` → 404 / 409 if `pending` / call AI delete first (AI failure → **503**, nothing removed) → remove file → delete row → 204.
- `main.py`: include router; `lifespan` calls `mark_interrupted_ingests` after `seed_admin`.

## 2.10 Tests

Fixtures are **generated at test time** in `tmp_path` (PyMuPDF text PDF with 3 pages, image-only PDF, python-docx with headings, TXT, MD) — no binaries committed.

| File | Covers |
|---|---|
| `ai/tests/test_parsers.py` | one per type; real PDF page numbers; image-only PDF → exact scanned message; DOCX/MD section numbering; cp1252 TXT; unsupported suffix; empty doc |
| `ai/tests/test_chunker.py` | parent sizes ≤ target (+ tolerance), children ≈ 300 tokens, overlap ≈ 50 tokens between consecutive siblings, every child's `parent_id` exists, child text ⊂ parent text, children cover the parent, page mapping across a page boundary, deterministic ids |
| `ai/tests/test_vector_store.py` | real `QdrantClient(":memory:")`: collection 1024/cosine, dim mismatch raises, payload shape, delete-by-`doc_id` leaves other docs |
| `ai/tests/test_docstore.py` | replace / ordered get / delete / count on a temp db |
| `ai/tests/test_ollama_client.py` | fake `ollama.Client`: options always carry `num_ctx`, `think=False`, `keep_alive`; batching; retry then success; 404 → "not pulled" no retry; wrong vector length |
| `ai/tests/test_ingest.py` | end to end over ASGI with in-memory Qdrant + temp docstore + fake embedder: `chunk_count` matches points, parents stored, re-ingest idempotent, delete clears both, outside-uploads path 400, scanned 422, Ollama down 503 with nothing written, no key 401 |
| `ai/tests/test_ollama_live.py` | `@pytest.mark.ollama`: real `bge-m3` returns 1024-dim vectors |
| `backend/tests/test_document_service.py` | `sanitize_filename` table (traversal, `\`, unicode, reserved-ish names, long names) |
| `backend/tests/test_admin_documents.py` | matrix: each allowed type 201 (`.PDF` upper-case ok), `.exe`/`.png`/no-ext 415, `.pdf` with text content 415, empty 422, over limit 413 (`MAX_UPLOAD_MB=1`), duplicate 409; file lands as `<id>_<safe>` inside `uploads/`; background ingest → `ready` / `failed` + error (monkeypatched `ai_client`); list; delete removes file+row, AI down → 503 and nothing removed, pending → 409, missing → 404; anonymous 401 and user 403 on all three routes; startup marks stale `pending` as `failed` |

Existing health tests are updated to patch the new `ollama_client` seam instead of `health_router.ollama.Client`.

## `CLAUDE.md` edits (same change, §16)
- §11 — new `ai/.env` vars (`EMBED_DIM`, three chunk-size vars) + re-index note; `.env.example` in sync.
- §6.1 — non-PDF `page` = heading-based section index; ingest idempotent per `doc_id`; AI rejects paths outside `data/uploads`.
- §9 — upload status codes (415/413/422/409), delete-while-pending 409, `DELETE /internal/documents` response body.
- §17 Phase 2 — tick 2.1–2.10, record the gate result; Settled decisions — stale `pending` recovery on restart.

## Verification

Automated, in `ai/` and `backend/`:
```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
uv run pytest -m ollama     # ai/ only, with Ollama running
```

Manual gate (three terminals: Ollama running, AI service without `--reload`, backend):
1. `/api/health` still `ok`; AI startup log shows the `chunks` collection ready.
2. Log in as admin; `POST /api/admin/documents` with **the real PDF you provide** → 201 `pending`; poll `GET` until `ready`; `chunk_count` ≈ characters / ~1000 (sanity range); AI log shows parse/chunk/embed timings; `ollama ps` shows `bge-m3` at 100% GPU.
3. Re-upload the same file → 409. Upload a `.png` → 415. Upload an image-only PDF → row ends `failed` with "scanned or image-only PDF is not supported".
4. Upload a small DOCX and MD → `ready`.
5. `DELETE` the PDF → 204; Qdrant count and docstore count for that `doc_id` are 0 (checked via a short `uv run python` snippet with the AI service stopped, or a debug log line); file gone from `data/uploads/`.
6. A regular user hitting any `/api/admin/documents` route → 403.

## Risks
- **PDF layout noise** (headers/footers, two columns) degrades chunks; accepted for v1, observed on the real PDF at the gate and noted for 4.8.
- **`num_ctx` on `bge-m3`** may change its VRAM residency vs the §2 measurement — check `ollama ps` at the gate.
- **Qdrant local mode payload indexes are no-ops**; delete-by-filter scans — fine at this corpus size.
- **BackgroundTasks under `ASGITransport`**: tests assume the task completes before the response returns; if not, the tests call `run_ingest` directly.
