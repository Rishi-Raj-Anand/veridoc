"""The single wrapper around the ``ollama`` package (CLAUDE.md §6.6).

No other module may import ``ollama``. Every call sets ``num_ctx`` explicitly (Ollama's
default is smaller and silently truncates), disables Qwen3 thinking, and keeps the model
warm with ``keep_alive``. Failures surface as an ``OllamaError`` subclass whose message is
safe to show and whose ``code`` lets callers tell the causes apart (§9 ``X-Error-Code``).

Inside an ``llm_usage()`` scope every call is also recorded (purpose, tokens, seconds), so
a chat turn can report its prompt sizes and per-call latency (Phase 8.4).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypedDict, TypeVar

import httpx
import ollama

from ai_service.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

KEEP_ALIVE = "30m"
# One generation of <= ~1,000 tokens takes ~20 s warm plus ~7 s to swap the model in
# (§6.8), so 120 s only trips on a real hang - and well inside the backend's 300 s.
REQUEST_TIMEOUT_SECONDS = 120.0
# Below the backend's 8 s health timeout, so a hung Ollama reads "Ollama down" there.
LIST_TIMEOUT_SECONDS = 3.0
EMBED_BATCH_SIZE = 32
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 3.0)


class OllamaError(RuntimeError):
    """Ollama is unreachable, a model is missing, or it returned something unusable."""

    code = "ollama_error"


class OllamaUnreachableError(OllamaError):
    """Ollama is not running or refused the connection."""

    code = "ollama_unreachable"


class ModelNotPulledError(OllamaError):
    """The configured model is not pulled (Ollama answered 404)."""

    code = "model_missing"


class OllamaTimeoutError(OllamaError):
    """Ollama did not answer within the request timeout. Not retried: a stuck call stays stuck."""

    code = "ollama_timeout"


class LlmCall(TypedDict):
    """One recorded Ollama call (stored in transcripts and eval rows, never shown to users)."""

    purpose: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    seconds: float


_usage: ContextVar[list[LlmCall] | None] = ContextVar("llm_usage", default=None)


@contextmanager
def llm_usage() -> Iterator[list[LlmCall]]:
    """Record every Ollama call made in this context (and threads that copy it)."""
    calls: list[LlmCall] = []
    token = _usage.set(calls)
    try:
        yield calls
    finally:
        _usage.reset(token)


def _record(call: LlmCall) -> None:
    calls = _usage.get()
    if calls is not None:
        calls.append(call)


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


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, httpx.TimeoutException | TimeoutError)


def _is_connection_failure(exc: Exception) -> bool:
    return isinstance(exc, httpx.TransportError | ConnectionError) and not _is_timeout(exc)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


class OllamaClient:
    def __init__(
        self, settings: Settings, *, request_timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> None:
        self._settings = settings
        self._timeout = request_timeout
        self._client = ollama.Client(host=settings.ollama_host, timeout=request_timeout)

    def _unreachable(self, exc: Exception) -> OllamaUnreachableError:
        return OllamaUnreachableError(
            f"cannot reach Ollama at {self._settings.ollama_host} "
            f"({type(exc).__name__}) - is it running?"
        )

    def _call(self, description: str, model: str, fn: Callable[[], T]) -> T:
        """Run ``fn``, retrying connection failures and 5xx; map everything to ``OllamaError``."""
        attempts = len(RETRY_DELAYS_SECONDS) + 1
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except ollama.ResponseError as exc:
                if exc.status_code == 404:
                    raise ModelNotPulledError(
                        f"model '{model}' is not pulled - run: ollama pull {model}"
                    ) from exc
                if exc.status_code < 500 or attempt == attempts:
                    raise OllamaError(f"Ollama {description} failed: {exc.error}") from exc
                error: Exception = exc
            except Exception as exc:
                if _is_timeout(exc):
                    raise OllamaTimeoutError(
                        f"Ollama did not finish the {description} within {self._timeout:.0f} s"
                    ) from exc
                if not _is_connection_failure(exc):
                    raise OllamaError(
                        f"Ollama {description} failed ({type(exc).__name__}: {exc})"
                    ) from exc
                if attempt == attempts:
                    raise self._unreachable(exc) from exc
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
        prompt_tokens: int | None = None
        started = time.perf_counter()
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
            tokens = _int_or_none(getattr(response, "prompt_eval_count", None))
            if tokens is not None:
                prompt_tokens = (prompt_tokens or 0) + tokens
        wrong = {len(v) for v in vectors if len(v) != self._settings.embed_dim}
        if wrong:
            raise OllamaError(
                f"embedding model '{model}' returned {sorted(wrong)}-dim vectors but "
                f"EMBED_DIM={self._settings.embed_dim}"
            )
        if texts:
            _record(
                LlmCall(
                    purpose="embed",
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=None,
                    seconds=round(time.perf_counter() - started, 3),
                )
            )
        return vectors

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float,
        format: dict[str, Any] | None = None,
        purpose: str = "chat",
    ) -> str:
        """One non-streaming chat completion. ``purpose`` labels it in logs and usage."""
        model = self._settings.llm_model
        started = time.perf_counter()
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
        seconds = round(time.perf_counter() - started, 3)
        prompt_tokens = _int_or_none(getattr(response, "prompt_eval_count", None))
        completion_tokens = _int_or_none(getattr(response, "eval_count", None))
        logger.info(
            "ollama chat purpose=%s prompt_tokens=%s completion_tokens=%s seconds=%.2f",
            purpose,
            prompt_tokens,
            completion_tokens,
            seconds,
        )
        _record(
            LlmCall(
                purpose=purpose,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                seconds=seconds,
            )
        )
        return response.message.content or ""

    def list_models(self) -> set[str]:
        """Names of the models Ollama has pulled. One quick attempt, no retry (health)."""
        client = ollama.Client(host=self._settings.ollama_host, timeout=LIST_TIMEOUT_SECONDS)
        try:
            return listed_model_names(client.list())
        except Exception as exc:
            raise OllamaUnreachableError(
                f"cannot reach Ollama at {self._settings.ollama_host} ({type(exc).__name__}: {exc})"
            ) from exc
