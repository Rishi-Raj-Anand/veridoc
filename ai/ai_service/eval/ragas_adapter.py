"""RAGAS judge LLM and embeddings backed by our own ``OllamaClient`` (CLAUDE.md §6.6, §13).

Every Ollama call, the judge's included, goes through ``ai_service/llm/ollama_client.py``, so
the judge always runs with ``num_ctx=NUM_CTX``, ``think=False`` and ``keep_alive``. No
LangChain/OpenAI client is involved.

Importing this module imports ``ragas``, which is only installed with ``uv sync --group eval``.
The package ``__init__`` has already switched RAGAS telemetry off by then (§16).
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.outputs import Generation, LLMResult
from ragas.embeddings.base import BaseRagasEmbeddings
from ragas.llms.base import BaseRagasLLM
from ragas.run_config import RunConfig

from ai_service.llm.ollama_client import OllamaClient

# A judge call is one model swap (~7 s, §6.8) plus a few hundred output tokens. Our client
# already retries transient Ollama errors, so RAGAS' own retry is kept short.
JUDGE_TIMEOUT_SECONDS = 600
JUDGE_MAX_RETRIES = 2


def judge_run_config() -> RunConfig:
    """One worker: only one model fits in VRAM at a time (§6.8), so parallel calls just swap."""
    return RunConfig(
        timeout=JUDGE_TIMEOUT_SECONDS,
        max_retries=JUDGE_MAX_RETRIES,
        max_wait=10,
        max_workers=1,
    )


class OllamaRagasLLM(BaseRagasLLM):
    """``BaseRagasLLM`` over ``OllamaClient.chat``.

    A single completion runs at temperature 0, so the judge is deterministic. When RAGAS asks
    for ``n > 1`` completions (Response Relevancy generates several questions per answer), the
    temperature RAGAS chose is honoured, otherwise the n generations would be identical.
    """

    def __init__(self, client: OllamaClient, run_config: RunConfig | None = None) -> None:
        super().__init__()
        self.client = client
        self.set_run_config(run_config or judge_run_config())

    def _complete(self, prompt: Any, n: int, temperature: float | None) -> LLMResult:
        text = prompt.to_string()
        temp = 0.0 if n <= 1 or temperature is None else float(temperature)
        generations = [
            Generation(
                text=self.client.chat(
                    [{"role": "user", "content": text}], temperature=temp, purpose="judge"
                )
            )
            for _ in range(max(1, n))
        ]
        return LLMResult(generations=[generations])

    def generate_text(
        self,
        prompt: Any,
        n: int = 1,
        temperature: float = 0.01,
        stop: list[str] | None = None,
        callbacks: Any = None,
    ) -> LLMResult:
        return self._complete(prompt, n, temperature)

    async def agenerate_text(
        self,
        prompt: Any,
        n: int = 1,
        temperature: float | None = 0.01,
        stop: list[str] | None = None,
        callbacks: Any = None,
    ) -> LLMResult:
        return await asyncio.to_thread(self._complete, prompt, n, temperature)

    def is_finished(self, response: LLMResult) -> bool:
        return True


class OllamaRagasEmbeddings(BaseRagasEmbeddings):
    """``BaseRagasEmbeddings`` over ``OllamaClient.embed`` (``bge-m3``, batched)."""

    def __init__(self, client: OllamaClient, run_config: RunConfig | None = None) -> None:
        super().__init__()
        self.client = client
        self.set_run_config(run_config or judge_run_config())

    def embed_query(self, text: str) -> list[float]:
        return self.client.embed([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)
