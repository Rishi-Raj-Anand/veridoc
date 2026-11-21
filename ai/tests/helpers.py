"""Shared test helpers for the AI service."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from qdrant_client import QdrantClient

from ai_service.agents.generator import Generator
from ai_service.config import get_settings
from ai_service.graph.pipeline import build_pipeline
from ai_service.llm import ollama_client
from ai_service.llm.ollama_client import OllamaError
from ai_service.main import create_app
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.retriever import Retriever
from ai_service.retrieval.vector_store import VectorStore

INTERNAL_KEY = "test-internal-key"


class FakeQdrant:
    """Stand-in for the embedded QdrantClient that lifespan opens."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.closed = False

    def get_collections(self) -> object:
        if self.fails:
            raise RuntimeError("storage folder is already accessed by another instance")
        return object()

    def close(self) -> None:
        self.closed = True


class FakeOllamaClient:
    """Stand-in for ``ollama.Client``."""

    def __init__(self, models: list[str], *, fails: bool = False) -> None:
        self.models = models
        self.fails = fails

    def list(self) -> dict[str, Any]:
        if self.fails:
            raise ConnectionError("connection refused")
        return {"models": [{"model": name} for name in self.models]}


class FakeEmbedder:
    """Stand-in for ``OllamaClient``: deterministic 1024-dim vectors, optional failure."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fails:
            raise OllamaError("cannot reach Ollama at http://127.0.0.1:11434 - is it running?")
        return [[float(len(text) % 7 + 1)] + [0.0] * 1023 for text in texts]


class FakeLlm(FakeEmbedder):
    """Scripted ``OllamaClient``: ``embed`` returns ``query_vector``, ``chat`` returns ``reply``."""

    def __init__(
        self,
        *,
        query_vector: list[float] | None = None,
        reply: str = "",
        embed_fails: bool = False,
        chat_fails: bool = False,
    ) -> None:
        super().__init__(fails=embed_fails)
        self.query_vector = query_vector or [1.0] + [0.0] * 1023
        self.reply = reply
        self.chat_fails = chat_fails
        self.chat_calls: list[dict[str, Any]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fails:
            raise OllamaError("cannot reach Ollama at http://127.0.0.1:11434 - is it running?")
        return [list(self.query_vector) for _ in texts]

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        format: dict[str, Any] | None = None,
    ) -> str:
        self.chat_calls.append({"messages": messages, "temperature": temperature})
        if self.chat_fails:
            raise OllamaError("cannot reach Ollama at http://127.0.0.1:11434 - is it running?")
        return self.reply


def build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models: list[str] | None = None,
    ollama_fails: bool = False,
    qdrant: FakeQdrant | str | None = "default",
    llm: FakeEmbedder | None = None,
) -> FastAPI:
    """Create the app with Ollama and Qdrant replaced by fakes.

    ``ASGITransport`` does not run lifespan, so ``app.state.qdrant`` is set here by
    hand. Pass ``qdrant=None`` to simulate storage that never opened.
    """

    def client_factory(host: str, **_: Any) -> FakeOllamaClient:
        return FakeOllamaClient(models or [], fails=ollama_fails)

    monkeypatch.setattr(ollama_client.ollama, "Client", client_factory)
    app = create_app()
    app.state.qdrant = FakeQdrant() if qdrant == "default" else qdrant
    settings = get_settings()
    store = VectorStore(QdrantClient(":memory:"), settings)
    store.ensure_collection()
    app.state.vector_store = store
    app.state.docstore = Docstore(settings.docstore_path)
    app.state.llm = llm or FakeEmbedder()
    app.state.pipeline = build_pipeline(
        Retriever(store, app.state.llm, settings), Generator(app.state.llm, settings)
    )
    return app


def internal_client(app: FastAPI, *, key: str | None = INTERNAL_KEY) -> httpx.AsyncClient:
    """An HTTP client for the app, sending the internal key unless ``key=None``."""
    headers = {"X-Internal-Key": key} if key is not None else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    )
