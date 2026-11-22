"""Ollama wrapper (CLAUDE.md §6.6): options, batching, retry and error mapping."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import ollama
import pytest

from ai_service.config import Settings
from ai_service.llm import ollama_client
from ai_service.llm.ollama_client import (
    EMBED_BATCH_SIZE,
    REQUEST_TIMEOUT_SECONDS,
    ModelNotPulledError,
    OllamaClient,
    OllamaError,
    OllamaTimeoutError,
    OllamaUnreachableError,
    llm_usage,
)


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


# --- Phase 8.1: typed errors, timeouts, usage ---------------------------------------------


def test_errors_carry_a_code_per_cause() -> None:
    assert OllamaError("x").code == "ollama_error"
    assert OllamaUnreachableError("x").code == "ollama_unreachable"
    assert ModelNotPulledError("x").code == "model_missing"
    assert OllamaTimeoutError("x").code == "ollama_timeout"
    assert all(
        issubclass(cls, OllamaError)
        for cls in (OllamaUnreachableError, ModelNotPulledError, OllamaTimeoutError)
    )


def test_unreachable_is_its_own_type(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([ConnectionError("Failed to connect to Ollama")] * 3)

    with pytest.raises(OllamaUnreachableError, match="is it running"):
        make_client(monkeypatch, fake).chat([{"role": "user", "content": "x"}], temperature=0)


def test_missing_model_is_its_own_type(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([ollama.ResponseError("model not found", 404)])

    with pytest.raises(ModelNotPulledError, match="ollama pull qwen3:8b"):
        make_client(monkeypatch, fake).chat([{"role": "user", "content": "x"}], temperature=0)


def test_a_read_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([httpx.ReadTimeout("slow")] * 3)

    with pytest.raises(OllamaTimeoutError, match="within 120 s"):
        make_client(monkeypatch, fake).chat([{"role": "user", "content": "x"}], temperature=0)

    assert len(fake.calls) == 1  # re-asking a stuck generation would only triple the wait


def test_server_errors_give_up_after_the_last_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([ollama.ResponseError("overloaded", 503)] * 3)

    with pytest.raises(OllamaError, match="overloaded") as caught:
        make_client(monkeypatch, fake).embed(["a"])

    assert type(caught.value) is OllamaError
    assert len(fake.calls) == 3


def test_an_unexpected_exception_is_not_reported_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient([ValueError("bad payload")])

    with pytest.raises(OllamaError, match="ValueError: bad payload") as caught:
        make_client(monkeypatch, fake).embed(["a"])

    assert type(caught.value) is OllamaError
    assert len(fake.calls) == 1


def test_the_request_timeout_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeClient:
        seen.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(ollama_client.ollama, "Client", factory)
    OllamaClient(Settings(_env_file=None))
    assert seen["timeout"] == REQUEST_TIMEOUT_SECONDS == 120.0

    OllamaClient(Settings(_env_file=None), request_timeout=600.0)
    assert seen["timeout"] == 600.0


def test_list_models_failure_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Down:
        def list(self) -> Any:
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama_client.ollama, "Client", lambda **_kw: Down())

    with pytest.raises(OllamaUnreachableError, match="cannot reach Ollama"):
        OllamaClient(Settings(_env_file=None)).list_models()


def test_calls_are_recorded_only_inside_a_usage_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = SimpleNamespace(
        message=SimpleNamespace(content="ok"), prompt_eval_count=900, eval_count=12
    )
    embedded = SimpleNamespace(embeddings=[[0.1] * 1024], prompt_eval_count=7)
    client = make_client(monkeypatch, FakeClient([reply, embedded, reply]))

    with llm_usage() as calls:
        client.chat([{"role": "user", "content": "x"}], temperature=0, purpose="generate")
        client.embed(["a"])
    client.chat([{"role": "user", "content": "x"}], temperature=0)  # outside: not recorded

    assert [(c["purpose"], c["prompt_tokens"], c["completion_tokens"]) for c in calls] == [
        ("generate", 900, 12),
        ("embed", 7, None),
    ]
    assert all(c["seconds"] >= 0 for c in calls)
