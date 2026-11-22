"""Shared test helpers for the AI service."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from qdrant_client import QdrantClient

from ai_service.agents.classifier import IntentClassifier
from ai_service.agents.condenser import Condenser
from ai_service.agents.generator import Generator
from ai_service.agents.safety import SafetyChecker
from ai_service.agents.smalltalk import SmallTalk
from ai_service.agents.verifier import Verifier
from ai_service.behaviour import BehaviourStore
from ai_service.config import get_settings
from ai_service.graph.pipeline import build_pipeline
from ai_service.llm import ollama_client
from ai_service.llm.ollama_client import OllamaError
from ai_service.main import create_app
from ai_service.memory.chat_store import ChatStore
from ai_service.prompts import load_prompt
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


def intent_json(intent: str) -> str:
    """A classifier reply (§6.9)."""
    return json.dumps({"intent": intent})


def verifier_json(
    faithfulness: float = 1.0,
    relevance: float = 1.0,
    unsupported: list[str] | None = None,
    missing: str | None = None,
    verdict: str = "pass",
) -> str:
    """A Verifier reply in the §6.5 shape (passing by default)."""
    return json.dumps(
        {
            "faithfulness": faithfulness,
            "relevance": relevance,
            "unsupported_claims": unsupported or [],
            "missing_info": missing,
            "verdict": verdict,
        }
    )


class FakeLlm(FakeEmbedder):
    """Scripted ``OllamaClient``.

    ``embed`` returns ``query_vector``. ``chat`` calls are told apart by their system prompt
    and ``format``: classifier, small talk, condenser, Verifier, otherwise the Generator. Each
    kind replays its script in order and repeats the last entry once it runs out. ``events``
    records the call order (``embed`` / ``chat:classify`` / ``chat:gen`` / ``chat:verify`` ...).
    The classifier answers "medical" unless scripted otherwise.
    """

    def __init__(
        self,
        *,
        query_vector: list[float] | None = None,
        reply: str = "",
        replies: list[str] | None = None,
        verifier_replies: list[str] | None = None,
        condense_replies: list[str] | None = None,
        classify_replies: list[str] | None = None,
        smalltalk_replies: list[str] | None = None,
        embed_fails: bool = False,
        chat_fails: bool = False,
        chat_error: OllamaError | None = None,
    ) -> None:
        super().__init__(fails=embed_fails)
        self.query_vector = query_vector or [1.0] + [0.0] * 1023
        self._gen_replies = replies if replies is not None else [reply]
        self._verifier_replies = (
            verifier_replies if verifier_replies is not None else [verifier_json()]
        )
        self._condense_replies = condense_replies if condense_replies is not None else [""]
        self._condense_prompt = load_prompt("condense")
        self._classify_replies = (
            classify_replies if classify_replies is not None else [intent_json("medical")]
        )
        self._classify_prompt = load_prompt("classify")
        self._smalltalk_replies = (
            smalltalk_replies if smalltalk_replies is not None else ["Hello! I'm Doctor AI."]
        )
        self._smalltalk_prompt = load_prompt("smalltalk")
        self.chat_fails = chat_fails
        self.chat_error = chat_error
        self.chat_calls: list[dict[str, Any]] = []
        self.events: list[str] = []

    def _kind(self, call: dict[str, Any]) -> str:
        system = call["messages"][0]["content"]
        if call["format"] is not None:
            return "classify" if system == self._classify_prompt else "verify"
        if system == self._condense_prompt:
            return "condense"
        if system.startswith(self._smalltalk_prompt):
            return "smalltalk"
        return "gen"

    def _calls(self, kind: str) -> list[dict[str, Any]]:
        return [c for c in self.chat_calls if self._kind(c) == kind]

    @property
    def condense_calls(self) -> list[dict[str, Any]]:
        return self._calls("condense")

    @property
    def generator_calls(self) -> list[dict[str, Any]]:
        return self._calls("gen")

    @property
    def verifier_calls(self) -> list[dict[str, Any]]:
        return self._calls("verify")

    @property
    def classify_calls(self) -> list[dict[str, Any]]:
        return self._calls("classify")

    @property
    def smalltalk_calls(self) -> list[dict[str, Any]]:
        return self._calls("smalltalk")

    @property
    def rag_chat_calls(self) -> list[dict[str, Any]]:
        """``chat_calls`` without the intent classifier: the medical loop's own LLM calls."""
        return [c for c in self.chat_calls if self._kind(c) != "classify"]

    @property
    def rag_events(self) -> list[str]:
        """``events`` without the intent classifier: the medical loop's own call order."""
        return [e for e in self.events if e != "chat:classify"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        self.events.append("embed")
        if self.fails:
            raise OllamaError("cannot reach Ollama at http://127.0.0.1:11434 - is it running?")
        return [list(self.query_vector) for _ in texts]

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        format: dict[str, Any] | None = None,
        purpose: str = "chat",
    ) -> str:
        call = {
            "messages": messages,
            "temperature": temperature,
            "format": format,
            "purpose": purpose,
        }
        kind = self._kind(call)
        script = {
            "verify": self._verifier_replies,
            "condense": self._condense_replies,
            "classify": self._classify_replies,
            "smalltalk": self._smalltalk_replies,
            "gen": self._gen_replies,
        }[kind]
        self.chat_calls.append(call)
        self.events.append(f"chat:{kind}")
        if self.chat_error is not None:
            raise self.chat_error
        if self.chat_fails:
            raise OllamaError("cannot reach Ollama at http://127.0.0.1:11434 - is it running?")
        index = len(self._calls(kind))
        return script[min(index, len(script)) - 1]


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
    behaviour = BehaviourStore(settings.behaviour_path)
    app.state.behaviour_store = behaviour
    app.state.pipeline = build_pipeline(
        Retriever(store, app.state.docstore, app.state.llm, settings),
        Generator(app.state.llm, settings, behaviour),
        Verifier(app.state.llm, settings),
        Condenser(app.state.llm, settings),
        SafetyChecker.load(),
        settings,
        classifier=IntentClassifier(app.state.llm, settings),
        smalltalk=SmallTalk(app.state.llm, settings, behaviour),
        behaviour=behaviour,
    )
    app.state.chat_store = ChatStore(settings.chats_dir)
    return app


def internal_client(app: FastAPI, *, key: str | None = INTERNAL_KEY) -> httpx.AsyncClient:
    """An HTTP client for the app, sending the internal key unless ``key=None``."""
    headers = {"X-Internal-Key": key} if key is not None else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    )
