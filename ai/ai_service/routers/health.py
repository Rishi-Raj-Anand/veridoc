"""Internal health endpoint (CLAUDE.md §9).

Reports three independent checks - Ollama reachable, both required models pulled,
and Qdrant open - so a failure says *which* dependency is broken. It also reports how many
chunks are indexed, so the admin can see an empty knowledge base (Phase 8.1).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ai_service.config import Settings, get_settings
from ai_service.llm.ollama_client import OllamaClient, OllamaError, normalise_model_name
from ai_service.security import require_internal_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["health"],
    dependencies=[Depends(require_internal_key)],
)


class OllamaHealth(BaseModel):
    ok: bool
    host: str
    error: str | None = None


class ModelsHealth(BaseModel):
    llm: bool
    embed: bool
    missing: list[str] = []


class QdrantHealth(BaseModel):
    ok: bool
    path: str
    error: str | None = None


class KnowledgeBaseHealth(BaseModel):
    # Child chunks indexed; null when Qdrant is not open. 0 is not "degraded": the service
    # works, it just falls back on every medical question until documents are uploaded.
    chunks: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    ollama: OllamaHealth
    models: ModelsHealth
    qdrant: QdrantHealth
    knowledge_base: KnowledgeBaseHealth = KnowledgeBaseHealth()


async def check_ollama(settings: Settings) -> tuple[OllamaHealth, ModelsHealth]:
    """Ask Ollama for its model list and check both required models are present."""
    required = {
        "llm": normalise_model_name(settings.llm_model),
        "embed": normalise_model_name(settings.embed_model),
    }
    try:
        available = await asyncio.to_thread(OllamaClient(settings).list_models)
    except OllamaError as exc:  # health must report, never raise
        logger.warning("ollama unreachable at %s: %s", settings.ollama_host, exc)
        return (
            OllamaHealth(ok=False, host=settings.ollama_host, error=str(exc)),
            ModelsHealth(llm=False, embed=False, missing=sorted(required.values())),
        )
    return (
        OllamaHealth(ok=True, host=settings.ollama_host),
        ModelsHealth(
            llm=required["llm"] in available,
            embed=required["embed"] in available,
            missing=sorted(tag for tag in required.values() if tag not in available),
        ),
    )


async def check_qdrant(request: Request, settings: Settings) -> QdrantHealth:
    """Confirm the embedded Qdrant client opened at startup still answers."""
    path = str(settings.vectors_dir)
    client = getattr(request.app.state, "qdrant", None)
    if client is None:
        return QdrantHealth(ok=False, path=path, error="local storage was not opened at startup")
    try:
        await asyncio.to_thread(client.get_collections)
    except Exception as exc:  # health must report, never raise
        logger.warning("qdrant check failed at %s: %s", path, exc)
        return QdrantHealth(ok=False, path=path, error=f"{type(exc).__name__}: {exc}")
    return QdrantHealth(ok=True, path=path)


async def check_knowledge_base(request: Request) -> KnowledgeBaseHealth:
    store = getattr(request.app.state, "vector_store", None)
    if store is None:
        return KnowledgeBaseHealth()
    try:
        return KnowledgeBaseHealth(chunks=await asyncio.to_thread(store.count))
    except Exception as exc:  # health must report, never raise
        logger.warning("knowledge base count failed: %s", exc)
        return KnowledgeBaseHealth()


@router.get("/health", response_model=HealthResponse, summary="Ollama, models and Qdrant")
async def health(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    ollama_health, models_health = await check_ollama(settings)
    qdrant_health = await check_qdrant(request, settings)
    knowledge_base = (
        await check_knowledge_base(request) if qdrant_health.ok else KnowledgeBaseHealth()
    )
    healthy = ollama_health.ok and models_health.llm and models_health.embed and qdrant_health.ok
    return HealthResponse(
        status="ok" if healthy else "degraded",
        ollama=ollama_health,
        models=models_health,
        qdrant=qdrant_health,
        knowledge_base=knowledge_base,
    )
