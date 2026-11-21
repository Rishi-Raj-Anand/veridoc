"""Ollama wrapper (CLAUDE.md §6.6): options, batching, retry and error mapping."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import ollama
import pytest

from ai_service.config import Settings
from ai_service.llm import ollama_client
from ai_service.llm.ollama_client import EMBED_BATCH_SIZE, OllamaClient, OllamaError


class FakeClient:
    """Records calls; ``script`` yields a response or raises, once per call."""

    def __init__(self, script: list[Any] | None = None, dim: int = 1024) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.script = list(script or [])
        self.dim = dim

    def _next(self, default: Any) -> Any:
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return default

    def embed(self, **kwargs: Any) -> Any:
        self.calls.append(("embed", kwargs))
        count = len(kwargs["input"])
        return self._next(SimpleNamespace(embeddings=[[0.1] * self.dim for _ in range(count)]))

    def chat(self, **kwargs: Any) -> Any:
        self.calls.append(("chat", kwargs))
        return self._next(SimpleNamespace(message=SimpleNamespace(content="hi")))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ollama_client.time, "sleep", lambda _s: None)


def make_client(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> OllamaClient:
    monkeypatch.setattr(ollama_client.ollama, "Client", lambda **_kw: fake)
    return OllamaClient(Settings(_env_file=None))


def test_embed_always_sets_num_ctx_and_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient()
    client = make_client(monkeypatch, fake)

    vectors = client.embed(["a", "b"])

    assert len(vectors) == 2 and len(vectors[0]) == 1024
    _, kwargs = fake.calls[0]
    assert kwargs["model"] == "bge-m3"
    assert kwargs["options"] == {"num_ctx": 8192}
    assert kwargs["keep_alive"] == "30m"


def test_embed_batches_the_input(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient()
    client = make_client(monkeypatch, fake)

    vectors = client.embed([f"t{i}" for i in range(EMBED_BATCH_SIZE * 2 + 5)])

    assert len(vectors) == EMBED_BATCH_SIZE * 2 + 5
    assert [len(kw["input"]) for _, kw in fake.calls] == [EMBED_BATCH_SIZE, EMBED_BATCH_SIZE, 5]


def test_embed_of_nothing_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient()

    assert make_client(monkeypatch, fake).embed([]) == []
    assert fake.calls == []


def test_embed_rejects_wrong_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, FakeClient(dim=768))

    with pytest.raises(OllamaError, match="768"):
        client.embed(["a"])


def test_embed_rejects_wrong_vector_count(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([SimpleNamespace(embeddings=[[0.0] * 1024])])

    with pytest.raises(OllamaError, match="1 embeddings for 2 inputs"):
        make_client(monkeypatch, fake).embed(["a", "b"])


def test_chat_sets_options_think_false_and_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient()

    text = make_client(monkeypatch, fake).chat([{"role": "user", "content": "x"}], temperature=0.2)

    assert text == "hi"
    _, kwargs = fake.calls[0]
    assert kwargs["options"] == {"num_ctx": 8192, "temperature": 0.2}
    assert kwargs["think"] is False
    assert kwargs["keep_alive"] == "30m"
    assert kwargs["model"] == "qwen3:8b"


def test_transient_failure_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([httpx.ConnectError("refused"), ollama.ResponseError("busy", 503)])

    vectors = make_client(monkeypatch, fake).embed(["a"])

    assert len(vectors) == 1
    assert len(fake.calls) == 3


def test_gives_up_after_the_retries_with_a_friendly_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient([httpx.ConnectError("refused")] * 3)

    with pytest.raises(OllamaError, match="is it running"):
        make_client(monkeypatch, fake).embed(["a"])

    assert len(fake.calls) == 3


def test_missing_model_is_not_retried_and_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([ollama.ResponseError("model not found", 404)])

    with pytest.raises(OllamaError, match="ollama pull bge-m3"):
        make_client(monkeypatch, fake).embed(["a"])

    assert len(fake.calls) == 1


def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([ollama.ResponseError("bad request", 400)])

    with pytest.raises(OllamaError, match="bad request"):
        make_client(monkeypatch, fake).embed(["a"])

    assert len(fake.calls) == 1
