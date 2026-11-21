"""The single wrapper around the ``ollama`` package (CLAUDE.md §6.6).

No other module may import ``ollama``. Every call sets ``num_ctx`` explicitly (Ollama's
default is smaller and silently truncates), disables Qwen3 thinking, and keeps the model
warm with ``keep_alive``. Failures surface as ``OllamaError`` with a message safe to show.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import httpx
import ollama

from ai_service.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

KEEP_ALIVE = "30m"
REQUEST_TIMEOUT_SECONDS = 300.0
LIST_TIMEOUT_SECONDS = 5.0
EMBED_BATCH_SIZE = 32
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 3.0)


class OllamaError(RuntimeError):
    """Ollama is unreachable, a model is missing, or it returned something unusable."""


def normalise_model_name(name: str) -> str:
    """Add the implicit ``:latest`` tag Ollama reports but config omits.

    ``EMBED_MODEL=bge-m3`` is listed by Ollama as ``bge-m3:latest``, so comparing the
    raw strings would report a pulled model as missing.
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


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, ollama.ResponseError):
        return exc.status_code >= 500
    return isinstance(exc, httpx.TransportError | ConnectionError | TimeoutError)


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = ollama.Client(host=settings.ollama_host, timeout=REQUEST_TIMEOUT_SECONDS)

    def _call(self, description: str, model: str, fn: Callable[[], T]) -> T:
        """Run ``fn`` with retry on transient failures; map everything to ``OllamaError``."""
        attempts = len(RETRY_DELAYS_SECONDS) + 1
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except ollama.ResponseError as exc:
                if exc.status_code == 404:
                    raise OllamaError(
                        f"model '{model}' is not pulled - run: ollama pull {model}"
                    ) from exc
                if not _is_transient(exc) or attempt == attempts:
                    raise OllamaError(f"Ollama {description} failed: {exc.error}") from exc
                error: Exception = exc
            except Exception as exc:
                if not _is_transient(exc) or attempt == attempts:
                    raise OllamaError(
                        f"cannot reach Ollama at {self._settings.ollama_host} "
                        f"({type(exc).__name__}) - is it running?"
                    ) from exc
                error = exc
            delay = RETRY_DELAYS_SECONDS[attempt - 1]
            logger.warning(
                "ollama %s failed (%s), retry %d/%d in %.0fs",
                description,
                type(error).__name__,
                attempt,
                attempts - 1,
                delay,
            )
            time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` in batches; every vector must match ``EMBED_DIM``."""
        model = self._settings.embed_model
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = list(texts[start : start + EMBED_BATCH_SIZE])
            response = self._call(
                "embedding",
                model,
                lambda batch=batch: self._client.embed(
                    model=model,
                    input=batch,
                    options={"num_ctx": self._settings.num_ctx},
                    keep_alive=KEEP_ALIVE,
                ),
            )
            embeddings = [list(v) for v in response.embeddings]
            if len(embeddings) != len(batch):
                raise OllamaError(
                    f"Ollama returned {len(embeddings)} embeddings for {len(batch)} inputs"
                )
            vectors.extend(embeddings)
        wrong = {len(v) for v in vectors if len(v) != self._settings.embed_dim}
        if wrong:
            raise OllamaError(
                f"embedding model '{model}' returned {sorted(wrong)}-dim vectors but "
                f"EMBED_DIM={self._settings.embed_dim}"
            )
        return vectors

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float,
        format: dict[str, Any] | None = None,
    ) -> str:
        """One non-streaming chat completion (used from Phase 3)."""
        model = self._settings.llm_model
        response = self._call(
            "chat",
            model,
            lambda: self._client.chat(
                model=model,
                messages=list(messages),
                options={"num_ctx": self._settings.num_ctx, "temperature": temperature},
                think=False,
                keep_alive=KEEP_ALIVE,
                format=format,
            ),
        )
        return response.message.content or ""

    def list_models(self) -> set[str]:
        """Names of the models Ollama has pulled. One quick attempt, no retry (health)."""
        client = ollama.Client(host=self._settings.ollama_host, timeout=LIST_TIMEOUT_SECONDS)
        try:
            return listed_model_names(client.list())
        except Exception as exc:
            raise OllamaError(
                f"cannot reach Ollama at {self._settings.ollama_host} ({type(exc).__name__}: {exc})"
            ) from exc
