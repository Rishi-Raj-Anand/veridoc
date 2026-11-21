# Phase 1 — Auth & data model (Milestone 2)

| | |
|---|---|
| **Status** | ✅ **Complete — gate passed and merged to `main` 2026-11-21** |
| **Branch** | `feature/phase-1-auth` (merged, then deleted) |
| **Commits** | `f430898` · `0af1c83` · `4e9b0d1` · `d91cfc7` · `a2e7b24`, merged as `3f6038a` |
| **Tasks** | 1.1 – 1.6 |
| **Gate** | `uv run ruff check . ; uv run ruff format --check . ; uv run pytest` green in `backend/` |
| **Touches** | `backend/` only — no AI service, no frontend |
| **Spec** | `CLAUDE.md` §17 holds the live task list; this file is the Phase 1 record |

---

## Context

Phase 0 left a three-service skeleton with nothing behind it but health endpoints. There is
**no database at all**: `data/app.db` does not exist, Alembic is a pinned dependency but was
never initialised, and `backend/app/` has no `db.py`, `deps.py`, `models/`, or `routers/auth.py`.
Every frontend page is a stub and nobody can log in.

Phase 1 builds the floor everything else stands on: the three tables from §8, password hashing,
JWT issuing, the `user` / `admin` role split, and the single seeded admin account. It matters
beyond its own milestone because it unblocks the §17 two-track parallel split — Phase 2's
document upload needs `require_admin` and `documents.uploaded_by`, Phase 3's chat gateway needs
`chat_sessions` ownership, and Phase 6's route guards need the `role` in the login response.

Deliberately **not** in Phase 1: upload, ingestion, chat, and any AI-service contact. Nothing in
this phase calls Ollama or touches Qdrant.

## Decisions settled during planning

| Question | Decision |
|---|---|
| DB session style | **Sync `Session`, DB routes declared `def`** so FastAPI runs them in its own threadpool. No async driver added; Alembic autogenerate works unmodified. §14 gets one clarifying sentence. |
| Password policy | **8–128 characters, no composition rules.** Length over complexity; Argon2 has no bcrypt-style 72-byte ceiling. |
| `ADMIN_EMAIL` changed after first run | **Update the existing admin row in place** — guarantees one admin forever and preserves the row id that `documents.uploaded_by` will point at. |
| Role resolution per request | **Load the `users` row from the DB on every request.** A deleted account or changed role takes effect immediately; the token's `role` claim is informational only. |
| Email validation | **Add `email-validator==2.4.0`** (latest; pulls `dnspython`, `idna`) and use Pydantic `EmailStr`. Syntax-only by default — no DNS lookups on the request path. |
| Startup against an empty DB | **Fail fast** with the exact `alembic upgrade head` command. Keeps §8's "migrations only" rule; a half-working server is worse than a loud one. |

---

## Task 1.0 — Housekeeping

- Branch `feature/phase-1-auth` off `main`.
- Add `"email-validator==2.4.0"` to `backend/pyproject.toml` dependencies, then `uv sync`.
- Fill in this file's result sections as tasks land, the way
  `phase_0_skeleton_and_health.md` records its gate.

## Task 1.1 — `db.py`, models, Alembic

### `backend/app/db.py` (new)

Mirror `config.py`'s `@lru_cache` singleton style so tests can repoint `DATA_DIR` and reset:

```python
NAMING_CONVENTION = {            # stable constraint names: required for SQLite
    "ix": "ix_%(column_0_label)s",   # batch ALTER in Alembic, and keeps
    "uq": "uq_%(table_name)s_%(column_0_name)s",   # autogenerate diffs quiet
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

@lru_cache
def get_engine() -> Engine: ...        # sqlite:///{settings.db_path}, check_same_thread=False
@lru_cache
def get_sessionmaker() -> sessionmaker[Session]: ...   # expire_on_commit=False
```

- Engine creation is lazy (SQLAlchemy does not connect until first use), so importing `app.db`
  never creates a stray `app.db` file.
- **`PRAGMA foreign_keys=ON`** via an `event.listens_for(engine, "connect")` hook — SQLite
  disables FK enforcement by default, which would silently void `documents.uploaded_by` and
  `chat_sessions.user_id`.
- Expose `reset_engine()` (disposes and clears both caches) for the test fixture.

### `backend/app/models/` (new package)

`Base` lives in `db.py`; `models/__init__.py` re-exports `User`, `Document`, `ChatSession` so
`alembic/env.py` has one import to pull all metadata.

Schema exactly as §8, in SQLAlchemy 2.0 `Mapped` / `mapped_column` style:

- **`user.py`** — `id` int pk; `email` `String(320)` unique, **stored lower-cased**;
  `password_hash` `String(255)`; `role` `String(5)`; `created_at` `DateTime(timezone=True)`
  defaulting to `datetime.now(UTC)` in Python (not `server_default` — SQLite has no native
  timezone-aware now).
  Two table-level guards that encode §1's hard constraints in the schema itself:
  ```python
  CheckConstraint("role IN ('user', 'admin')", name="role_valid")
  Index("uq_users_single_admin", "role", unique=True, sqlite_where=text("role = 'admin'"))
  ```
  The partial unique index makes **"exactly one admin"** a database invariant, not just
  application logic.
- **`document.py`** — all §8 columns; `CheckConstraint` on `status IN ('pending','ready','failed')`;
  `sha256` `String(64)` unique; `chunk_count` default `0`; `error` nullable;
  `uploaded_by` FK → `users.id`. Built now, used in Phase 2.
- **`chat_session.py`** — `id` `String(36)` pk (UUID v4 text), `user_id` FK → `users.id`,
  `created_at`. Built now, used in task 3.5.

Add `Role = Literal["user", "admin"]` to `app/schemas/auth.py` and reuse it — matches the
existing `Status = Literal[...]` house style in `schemas/health.py`.

### Alembic

`uv run alembic init alembic` in `backend/`, then:

- **`alembic.ini`** — set `script_location = alembic`, `prepend_sys_path = .`, and **delete the
  hardcoded `sqlalchemy.url`** so there is one source of truth for the DB path.
- **`alembic/env.py`** — import `get_settings` and `Base`; set
  `config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.db_path}")`;
  `target_metadata = Base.metadata`; pass **`render_as_batch=True`** in both `context.configure`
  calls (SQLite cannot `ALTER` columns without Alembic's batch mode — this will matter the first
  time a column changes, and retrofitting it is painful).
- Generate the initial migration: `uv run alembic revision --autogenerate -m "initial schema"`,
  review it by hand (autogenerate does not always emit partial indexes correctly — confirm
  `uq_users_single_admin` carries its `sqlite_where`), then `uv run alembic upgrade head`.

## Task 1.2 — `services/auth_service.py`

Module-level functions taking `settings: Settings` explicitly, matching `ai_client.py`'s style
(no classes, no `Depends` inside the service layer).

- `_hasher = PasswordHasher()` module singleton; `hash_password(raw) -> str`;
  `verify_password(hash, raw) -> bool` swallowing `VerifyMismatchError` / `InvalidHashError` /
  `VerificationError` into `False` rather than letting them escape.
- `ALGORITHM = "HS256"` as a module constant (no new env var — consistent with
  `HEALTH_TIMEOUT_SECONDS` living in `ai_client.py`).
- `create_access_token(settings, user) -> str` with claims `sub` (**`str(user.id)`** — pyjwt
  requires a string subject), `role`, `iat`, `exp` = now + `JWT_EXPIRE_MINUTES`.
- `decode_access_token(settings, token) -> TokenPayload`, raising a local
  `class AuthError(RuntimeError)` for expired *and* invalid tokens with distinct messages.
- `DUMMY_HASH`: a precomputed Argon2 hash verified when a login email is not found, so a
  missing account and a wrong password take the same time and the endpoint does not leak which
  emails are registered.
- Never log the raw password, the hash, or the token (§14).

## Task 1.3 — Schemas and `routers/auth.py`

**`app/schemas/auth.py`** — request bodies use `ConfigDict(extra="forbid")` so a client that
posts `{"email", "password", "role": "admin"}` gets a **422**, making §8's "registration always
creates `role='user'`" rule structurally impossible to violate rather than merely untested:

```python
class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class LoginRequest(BaseModel):   # same shape, extra="forbid"
class TokenResponse(BaseModel):  # access_token, token_type="bearer", role  (§9 exactly)
class UserResponse(BaseModel):   # id, email, role, created_at; from_attributes=True
```

**`app/routers/auth.py`** — `APIRouter(prefix="/auth", tags=["auth"])`, included in `create_app()`
with `prefix="/api"` (the health router's precedent: only the shared `/api` lives in `main.py`).
Handlers are `def`, each with both `response_model=` and a matching return annotation.

| Route | Behaviour |
|---|---|
| `POST /api/auth/register` → 201 | Lower-case the email; **409** if it is already taken **or** equals `settings.admin_email` (a user must not be able to squat the admin address); hash with Argon2; insert `role="user"`; return `UserResponse`. |
| `POST /api/auth/login` → 200 | Look up by lower-cased email, verify (dummy-hash path when absent), **401 `"incorrect email or password"`** for both failure modes; return `{access_token, token_type, role}`. |
| `GET /api/auth/me` → 200 | Returns `UserResponse` for `get_current_user`. |

Log successful register/login at INFO with the email only — never the password or token.

## Task 1.4 — `app/deps.py`

Closest existing template is `ai/ai_service/security.py`: a guard dependency in its own module,
credentials declared **optional so absence yields 401, not FastAPI's 403/422**.

- `get_db() -> Iterator[Session]` — yield from `get_sessionmaker()`, close in `finally`.
- `get_current_user(settings, db, credentials)` using
  `HTTPBearer(auto_error=False)`: missing, malformed, expired, or well-signed-but-unknown-`sub`
  all → **401** with `WWW-Authenticate: Bearer`. The role is read off the **freshly loaded row**,
  so the token's claim is never the authority.
- `require_admin(user)` → the user, or **403 `"admin privileges required"`**.
- Export the `Annotated` aliases the later phases will lean on:
  `DbSession`, `CurrentUser`, `AdminUser`.

## Task 1.5 — Startup admin seeding

`seed_admin(db, settings)` goes in **`services/auth_service.py`** (auth domain — avoids adding a
service module §5 does not list). Called from `lifespan` in `main.py` right after
`ensure_data_dirs`. Note `lifespan`'s parameter is currently named `_app`; it stays unused.

Order of operations, all idempotent:

1. **Guard the schema.** If the `users` table is absent (`sqlalchemy.inspect`), raise
   `RuntimeError("database not initialised - run 'uv run alembic upgrade head' in backend/")`.
   This is the decided fail-fast behaviour and mirrors the AI service's Qdrant hint.
2. Select the single `role='admin'` row.
   - **Absent** → insert it with the configured email + Argon2 hash.
   - **Present** → update `email` in place if it differs; rehash only when
     `verify_password(row.password_hash, settings.admin_password)` returns `False`. Argon2 salts
     every hash, so comparing hashes cannot detect a changed password — verifying the plaintext
     is the only correct test, and it also makes the no-op case a true no-op.
3. Commit only when something actually changed, and log **what** changed
   (`"admin email updated"` / `"admin password updated"`) — never the value.
4. `settings.insecure_defaults` already warns about a placeholder `ADMIN_PASSWORD`; the existing
   warning in `lifespan` covers it.

Because the §1 partial unique index exists, a bug that tried to insert a second admin fails at
the database rather than silently creating one.

## Task 1.6 — Tests

**`tests/conftest.py`** needs a real fix, not just an addition. Today its only fixture clears the
settings cache; it does **not** redirect `DATA_DIR`. Phase 0 got away with that because
`ASGITransport` never runs lifespan and no code opened a database — from Phase 1 on, a test that
touches the DB would write to the developer's real `data/app.db`. Add an autouse fixture in the
shape of `ai/tests/conftest.py`:

```python
monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
monkeypatch.setenv("JWT_SECRET", "test-secret")
monkeypatch.setenv("ADMIN_EMAIL", "admin@test.local")
monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
get_settings.cache_clear(); reset_engine()      # before and after each test
```

then `Base.metadata.create_all(get_engine())` for speed, with migration fidelity covered by its
own test (below).

**`tests/helpers.py`** (new, the `ai/tests/helpers.py` analogue) — `build_app()`,
`client(app)` returning an `httpx.AsyncClient` over `ASGITransport`, `register(client, ...)`,
`login(client, ...)`, and `bearer(token)` producing the `Authorization` header.

Use `app.dependency_overrides[get_db]` for session injection — `monkeypatch.setattr` on the
importing module (Phase 0's approach for `fetch_health`) does not fit a yielding dependency.

| File | Covers |
|---|---|
| `test_models.py` | `role` CHECK rejects a bad value; the partial index rejects a **second admin**; FK enforcement is actually on (insert a document with a bogus `uploaded_by` → `IntegrityError`); `created_at` is timezone-aware. |
| `test_migrations.py` | `alembic upgrade head` against a temp DB produces the same tables/columns as `Base.metadata` — catches a migration drifting from the models. |
| `test_auth_service.py` | hash → verify round trip; wrong password → `False`; garbage hash → `False` not an exception; token round trip carries `sub`/`role`; **expired token** (build `Settings(jwt_expire_minutes=-1)`) → `AuthError`; tampered signature → `AuthError`. |
| `test_auth_routes.py` | register 201; **duplicate email → 409**; duplicate differing only in case → 409; registering the admin email → 409; password of 7 chars → 422; **`role` in the body → 422**; login happy path returns `role`; **wrong password → 401**; unknown email → 401 with the identical message; `me` with a good token; **missing / malformed / expired token → 401**. |
| `test_deps.py` | **a `user` hitting a `require_admin` route → 403**; an admin → 200; a token whose `sub` no longer exists → 401 (proves the role comes from the DB, not the claim). Register a throwaway admin-guarded route on a test app rather than waiting for Phase 2's real one. |
| `test_admin_seed.py` | seeds once; **running twice changes nothing** (same row id, same hash); a changed `ADMIN_PASSWORD` rehashes while the id stays; a changed `ADMIN_EMAIL` updates in place and still leaves exactly one admin; seeding against an empty schema raises with the `alembic upgrade head` hint. |

## `CLAUDE.md` edits (same change, per §16)

- **§3 pinned-versions table** — add `email-validator==2.4.0` with the reason (`EmailStr`
  needs it; pulls `dnspython` + `idna`).
- **§11** — note that no new backend env vars were needed; `.env.example` is unchanged.
- **§14 Python conventions** — amend "Async endpoints" to record that **DB-bound backend routes
  are sync `def`** so FastAPI threadpools them, since SQLite has no async driver in this project;
  genuinely async I/O (the AI-service HTTP calls) stays `async def`.
- **§12** — record that the backend now **fails to start** against an uninitialised database, and
  that `uv run alembic upgrade head` is required once before the first run.
- **§17 Phase 1** — tick 1.1–1.6 and record the gate result, matching how Phase 0 is annotated.
- **§8** — add the two schema-level invariants that go beyond the prose: the `role` CHECK
  constraint and the partial unique index enforcing a single admin.

## Verification

Automated, from `backend/`:

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
```

Manual end-to-end — the §17 gate is "`uv run pytest` green", but prove the real wiring too:

```powershell
cd backend
uv run alembic upgrade head          # creates data/app.db
uv run uvicorn app.main:app --reload --port 8000
```

1. Startup log shows the resolved absolute `data dir` and an admin seeding line.
2. `POST /api/auth/register` with a fresh email → 201, `role` is `user`.
3. The same email again → **409**; a 7-character password → **422**; adding `"role": "admin"`
   to the body → **422**.
4. `POST /api/auth/login` → token + `"role": "user"`. `GET /api/auth/me` with
   `Authorization: Bearer <token>` → the user; with no header and with a mangled token → **401**.
5. Log in as `ADMIN_EMAIL` → `"role": "admin"`.
6. Stop the server, change `ADMIN_PASSWORD` in `.env`, restart → the log reports the password
   update, the old password no longer logs in, the new one does, and
   `SELECT COUNT(*) FROM users WHERE role='admin'` is still **1**.
7. Restart again with nothing changed → no update logged (idempotency).
8. Delete `data/app.db` and start without migrating → the server **refuses to boot** and names
   `uv run alembic upgrade head`.
9. `GET /api/health` still returns `ok` (Phase 0 untouched).

## Risks

- **Alembic autogenerate and the partial index** — the `sqlite_where` clause is the one thing
  most likely to be dropped from the generated migration. Review the file before upgrading, and
  `test_models.py`'s second-admin test plus `test_migrations.py` together catch it.
- **The real `.env` leaking into tests** — `pydantic-settings` reads `.env` relative to the
  working directory, and pytest runs from `backend/`. The autouse `monkeypatch.setenv` fixture
  wins over the file, but any test that constructs settings directly must keep using
  `Settings(_env_file=None, ...)` as Phase 0 does.
- **`ADMIN_PASSWORD` is currently `12345`** in the local `.env` (noted in the Phase 0 record). It
  will fail the new 8-character minimum if it is ever routed through `RegisterRequest`. Seeding
  bypasses the schema by design, but set a real password during this phase and record that.

## Result (2026-11-21)

**Gate passed:** `ruff check`, `ruff format --check` clean, **58 tests pass** with no warnings
(14 from Phase 0 + 44 new). Also verified over real HTTP against a freshly migrated database:
register 201 / duplicate 409 / short password 422 / `role` in body 422, login and `/me`, missing
and mangled token 401, admin login, an unchanged restart logs nothing, a changed `ADMIN_PASSWORD`
updates the admin in place (old password rejected, exactly one admin remains), and an unmigrated
database refuses to boot naming `uv run alembic upgrade head`.

### Deviations from the plan

| Planned | Actual | Why |
|---|---|---|
| `LoginRequest.email: EmailStr` | plain bounded `str` | `email-validator` rejects special-use domains. The seeded admin uses `admin@veridoc.local` (the `.env.example` default), so an `EmailStr` login would have returned 422 for the admin. Caught by running the admin login, not by the first test pass. Registration still uses `EmailStr`. |
| `app.dependency_overrides[get_db]` in tests | not needed | the autouse fixture points `DATA_DIR` at a temp dir, so `get_sessionmaker()` already resolves to a throwaway database |
| Test addresses on `.local` | `example.com` | same `email-validator` rule, applied to registration |
| `seed_admin` as specified | plus a clear error | if `ADMIN_EMAIL` collides with an existing regular user, it raises `cannot seed the admin: ... already belongs to a regular user` instead of a raw `IntegrityError` |
| `test_config` untouched | one test now clears three env vars | the new autouse fixture sets `JWT_SECRET` / `ADMIN_PASSWORD`, which `test_insecure_defaults_are_reported` asserts are still defaults |
| Manual check via a server + curl | in-process script (uvicorn + `httpx`, throwaway `DATA_DIR`) | avoids touching the real database and needs no process killing; the server stops through uvicorn's own `should_exit` |
| 32-byte JWT secret in tests | added | pyjwt warns on short HMAC keys; the 1-byte test secrets produced 20 warnings |

### State left behind

- `data/app.db` was migrated (`alembic upgrade head`) during verification, but **no admin is seeded
  yet** — that happens the first time the backend starts. Nothing else was written to it.
- `ADMIN_PASSWORD` in the local `.env` is still the weak `12345`. Seeding bypasses the 8-character
  rule by design; change it before any non-local exposure.
- Committed as five commits (dependency, data model, auth, tests, docs) and merged to `main` with a
  no-fast-forward merge commit, `3f6038a`. The feature branch was then deleted; `main` re-tested at
  58 passing.

## Next — Phase 2: Ingestion (Milestone 3)

Once this lands the §17 work split opens up: **Track A** takes 2.1–2.6 on the AI service
(parsers, chunker, Qdrant, docstore, Ollama client, ingest router) and **Track B** takes
2.7–2.10 on the backend (document service, AI client, admin router, tests), since `require_admin`
and `documents.uploaded_by` now exist.
