"""Internal health endpoint (CLAUDE.md §9).

Reports three independent checks - Ollama reachable, both required models pulled,
and Qdrant open - so a failure says *which* dependency is broken.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

import ollama
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ai_service.config import Settings, get_settings
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


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    ollama: OllamaHealth
    models: ModelsHealth
    qdrant: QdrantHealth


def normalise_model_name(name: str) -> str:
    """Add the implicit ``:latest`` tag Ollama reports but config omits.

    ``EMBED_MODEL=bge-m3`` is pulled and listed by Ollama as ``bge-m3:latest``, so
    comparing the raw strings would report a pulled model as missing.
    """
    return name if ":" in name else f"{name}:latest"


def listed_model_names(listed: Any) -> set[str]:
    """Extract model tags from an Ollama ``list()`` response, object or dict form."""
    entries = getattr(listed, "models", None)
    if entries is None and isinstance(listed, dict):
        entries = listed.get("models", [])
    names: set[str] = set()
    for entry in entries or []:
        name = getattr(entry, "model", None)
        if name is None and isinstance(entry, dict):
            name = entry.get("model") or entry.get("name")
        if name:
            names.add(normalise_model_name(str(name)))
    return names


async def check_ollama(settings: Settings) -> tuple[OllamaHealth, ModelsHealth]:
    """Ask Ollama for its model list and check both required models are present."""
    required = {
        "llm": normalise_model_name(settings.llm_model),
        "embed": normalise_model_name(settings.embed_model),
    }
    client = ollama.Client(host=settings.ollama_host)
    try:
        listed = await asyncio.to_thread(client.list)
    except Exception as exc:  # noqa: BLE001 - health must report, never raise
        logger.warning("ollama unreachable at %s: %s", settings.ollama_host, exc)
        return (
            OllamaHealth(ok=False, host=settings.ollama_host, error=f"{type(exc).__name__}: {exc}"),
            ModelsHealth(llm=False, embed=False, missing=sorted(required.values())),
        )

    available = listed_model_names(listed)
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
    except Exception as exc:  # noqa: BLE001 - health must report, never raise
        logger.warning("qdrant check failed at %s: %s", path, exc)
        return QdrantHealth(ok=False, path=path, error=f"{type(exc).__name__}: {exc}")
    return QdrantHealth(ok=True, path=path)


@router.get("/health", response_model=HealthResponse, summary="Ollama, models and Qdrant")
async def health(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    ollama_health, models_health = await check_ollama(settings)
    qdrant_health = await check_qdrant(request, settings)
    healthy = ollama_health.ok and models_health.llm and models_health.embed and qdrant_health.ok
    return HealthResponse(
        status="ok" if healthy else "degraded",
        ollama=ollama_health,
        models=models_health,
        qdrant=qdrant_health,
    )
