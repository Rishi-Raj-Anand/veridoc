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
| An answer takes 30–90 s | Normal: the LLM and the embedding model swap in 8 GB of VRAM (CLAUDE.md §6.8). Small talk and refusals are near-instant. |

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
