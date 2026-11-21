"""Health response contracts (CLAUDE.md §9; frozen in task 0.4).

``AiServiceHealth`` mirrors the AI service's ``/internal/health`` body so the
backend validates what it proxies instead of passing an untyped dict through.
Every model allows extra keys so the AI service can add fields without breaking
the backend.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Status = Literal["ok", "degraded"]


class OllamaHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    host: str
    error: str | None = None


class ModelsHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    llm: bool
    embed: bool
    missing: list[str] = []


class QdrantHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    path: str
    error: str | None = None


class AiServiceHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Status
    ollama: OllamaHealth
    models: ModelsHealth
    qdrant: QdrantHealth


class HealthResponse(BaseModel):
    status: Status
    backend: Literal["ok"] = "ok"
    ai_service: AiServiceHealth | None = None
    error: str | None = None
