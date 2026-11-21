"""Iteration-1 retrieval (CLAUDE.md §6.3): Top-K child chunks behind a score gate.

Hits are grouped by ``(filename, page)`` so a citation number means one place in one
document: three chunks from the same page are source ``[1]``, not ``[1] [2] [3]``. Kept free
of LangGraph so the escalation levels of task 4.4 can extend it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.retrieval.vector_store import SearchHit, VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    """One citable place: ``n`` maps to ``(filename, page)``; ``text`` joins its chunks."""

    n: int
    filename: str
    page: int
    text: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    sources: list[Source]
    best_score: float
    passed_gate: bool


def group_sources(hits: list[SearchHit]) -> list[Source]:
    """Number hits by best score, one number per ``(filename, page)``."""
    order: list[tuple[str, int]] = []
    texts: dict[tuple[str, int], list[str]] = {}
    scores: dict[tuple[str, int], float] = {}
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        key = (hit.filename, hit.page)
        if key not in texts:
            order.append(key)
            texts[key] = []
            scores[key] = hit.score
        if hit.text not in texts[key]:
            texts[key].append(hit.text)
    return [
        Source(n=i, filename=key[0], page=key[1], text="\n\n".join(texts[key]), score=scores[key])
        for i, key in enumerate(order, start=1)
    ]


def format_context(sources: list[Source]) -> str:
    """The ``Sources:`` block the Generator reads."""
    return "\n\n".join(f"[{s.n}] {s.filename}, p. {s.page}\n{s.text}" for s in sources)


class Retriever:
    def __init__(self, store: VectorStore, llm: OllamaClient, settings: Settings) -> None:
        self._store = store
        self._llm = llm
        self._settings = settings

    def retrieve(self, query: str) -> RetrievalResult:
        """Embed ``query`` and return the Top-``top_k_precision`` chunks, gated on score."""
        vector = self._llm.embed([query])[0]
        hits = self._store.search(vector, self._settings.top_k_precision)
        best = max((h.score for h in hits), default=0.0)
        passed = bool(hits) and best >= self._settings.min_retrieval_score
        logger.info(
            "retrieve hits=%d best=%.3f gate=%s (min %.2f)",
            len(hits),
            best,
            "pass" if passed else "FAIL",
            self._settings.min_retrieval_score,
        )
        return RetrievalResult(sources=group_sources(hits), best_score=best, passed_gate=passed)
