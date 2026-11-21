# VeriDoc AI

A self-correcting **dual-agent RAG** system. A **Generator** drafts answers from retrieved
document chunks; a **Verifier** audits each draft for faithfulness and relevance. If a draft
fails, retrieval is escalated (wider Top-K, then parent chunks) for up to 3 iterations, after
which the system returns a deterministic safe fallback instead of guessing.

**v1 — "Doctor AI":** an admin uploads medical reference documents; users chat with an
assistant that answers **only** from those documents, with citations. All AI runs locally
through Ollama — no cloud LLM or embedding APIs.

B.Tech CSE Major Project — Rishi Raj Anand (23103121), Vedant Salve (23117040).
Mentor: Dr. Jagdeep Kaur.

> ⚠️ VeriDoc AI provides information from reference documents and is not a substitute for
> professional medical advice, diagnosis, or treatment.

## Architecture

```
frontend (React, :5173)  ->  backend (FastAPI, :8000)  ->  ai service (FastAPI, :8001)
                                    |                              |
                              SQLite app.db                 Qdrant + docstore
                                                                   |
                                                       Ollama (:11434) qwen3:8b, bge-m3
```

The frontend talks only to the backend. The backend owns auth, users and uploads. The AI
service owns parsing, embeddings, Qdrant and the agent pipeline. See `CLAUDE.md` §4.

## Target hardware

| Component | Spec |
|---|---|
| GPU | NVIDIA RTX 5050 Laptop, 8 GB VRAM |
| RAM | 16 GB |
| CPU | AMD Ryzen 7 250 |
| OS | Windows 64-bit |

`qwen3:8b` (6.2 GB resident at `num_ctx=8192`) and `bge-m3` (689 MB) cannot both stay in
8 GB VRAM, so Ollama swaps them at ~7 s per switch. Expect 30–90 s per answer. See
`CLAUDE.md` §6.8.

## Prerequisites

1. **Ollama for Windows**, running, with both models pulled:
   ```powershell
   ollama pull qwen3:8b
   ollama pull bge-m3
   ```
2. **uv** (Python toolchain — provisions Python 3.12 itself; the system Python is not used):
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Open a new terminal afterwards so `uv` is on `PATH`.
3. **Node.js** 20+ and npm.

## Setup

Copy each `.env.example` to `.env` and fill in the secrets (`JWT_SECRET`, `ADMIN_PASSWORD`,
and the same `INTERNAL_API_KEY` in both `backend/.env` and `ai/.env`):

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item ai\.env.example ai\.env
Copy-Item frontend\.env.example frontend\.env
```

## Running

Windows PowerShell 5.1 has no `&&` — chain with `;`. Run each service in its own terminal.

```powershell
# terminal 1 - AI service (never use --reload: Qdrant holds an exclusive storage lock)
cd ai ; uv sync ; uv run uvicorn ai_service.main:app --host 127.0.0.1 --port 8001

# terminal 2 - backend
cd backend ; uv sync ; uv run alembic upgrade head ; uv run uvicorn app.main:app --reload --port 8000

# terminal 3 - frontend
cd frontend ; npm install ; npm run dev
```

Then open http://localhost:5173 and check http://localhost:8000/api/health.

## Quality checks

```powershell
cd backend ; uv run ruff check . ; uv run ruff format --check . ; uv run pytest
cd ai      ; uv run ruff check . ; uv run ruff format --check . ; uv run pytest
cd frontend ; npm run lint ; npm run typecheck ; npm run test
```

## Documentation

`CLAUDE.md` is the authoritative spec: architecture (§4), AI pipeline (§6), medical safety
rules (§7), API contracts (§9), environment variables (§11), commands (§12) and the task
breakdown (§17).
