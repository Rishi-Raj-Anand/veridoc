"""Retrieval with escalation levels (CLAUDE.md §6.3).

Iteration 1 (*precision*): Top-K child chunks, grouped by ``(filename, page)``.
Iterations 2 and 3 (*expansion*): a wider Top-K of children, widened to their **parent**
chunks (de-duplicated, ordered by best child score, capped). The score gate always judges the
best *child* score. A citation number means one place in one document, so sources are grouped
by ``(filename, page)`` at every level. Kept free of LangGraph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.retrieval.docstore import Docstore, StoredParent
from ai_service.retrieval.vector_store import SearchHit, VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    """One citable place: ``n`` maps to ``(filename, page)``; ``text`` joins its chunks.

    ``page`` is where the text starts (the page a citation names); ``page_end`` is set only
    when a parent chunk spans several pages.
    """

    n: int
    filename: str
    page: int
    text: str
    score: float
    page_end: int | None = None

    @property
    def page_label(self) -> str:
        if self.page_end is not None and self.page_end > self.page:
            return f"{self.page}-{self.page_end}"
        return str(self.page)


@dataclass(frozen=True)
class RetrievalResult:
    sources: list[Source]
    best_score: float
    passed_gate: bool
    mode: Literal["precision", "expansion"] = "precision"


def group_sources(hits: list[SearchHit]) -> list[Source]:
    """Number child hits by best score, one number per ``(filename, page)``."""
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


def ordered_parent_ids(hits: list[SearchHit], limit: int) -> list[str]:
    """Unique parent ids ordered by their best child score, at most ``limit``."""
    seen: list[str] = []
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        if hit.parent_id not in seen:
            seen.append(hit.parent_id)
    return seen[:limit]


def group_parents(
    hits: list[SearchHit], parents: dict[str, StoredParent], parent_ids: list[str]
) -> list[Source]:
    """Turn the chosen parents into numbered sources, one per ``(filename, page_start)``.

    A parent missing from the docstore is replaced by its own child chunks (and logged), so
    a docstore gap degrades to precision-level context instead of dropping evidence.
    """
    order: list[tuple[str, int]] = []
    texts: dict[tuple[str, int], list[str]] = {}
    scores: dict[tuple[str, int], float] = {}
    ends: dict[tuple[str, int], int] = {}
    by_score = sorted(hits, key=lambda h: h.score, reverse=True)
    for parent_id in parent_ids:
        children = [h for h in by_score if h.parent_id == parent_id]
        parent = parents.get(parent_id)
        if parent is not None:
            filename, start, end, text = (
                parent.filename,
                parent.page_start,
                parent.page_end,
                parent.text,
            )
        else:
            logger.warning("parent %s missing from the docstore; using its child chunks", parent_id)
            filename = children[0].filename
            start = min(c.page for c in children)
            end = max(c.page for c in children)
            text = "\n\n".join(dict.fromkeys(c.text for c in children))
        key = (filename, start)
        if key not in texts:
            order.append(key)
            texts[key] = []
            scores[key] = children[0].score
            ends[key] = end
        if text not in texts[key]:
            texts[key].append(text)
        ends[key] = max(ends[key], end)
    return [
        Source(
            n=i,
            filename=key[0],
            page=key[1],
            text="\n\n".join(texts[key]),
            score=scores[key],
            page_end=ends[key] if ends[key] > key[1] else None,
        )
        for i, key in enumerate(order, start=1)
    ]


def format_context(sources: list[Source]) -> str:
    """The ``Sources:`` block the Generator and the Verifier both read."""
    return "\n\n".join(f"[{s.n}] {s.filename}, p. {s.page_label}\n{s.text}" for s in sources)


class Retriever:
    def __init__(
        self, store: VectorStore, docstore: Docstore, llm: OllamaClient, settings: Settings
    ) -> None:
        self._store = store
        self._docstore = docstore
        self._llm = llm
        self._settings = settings

    def embed_query(self, query: str) -> list[float]:
        """The query vector; a separate step so the pipeline can reuse it (§6.8: one fewer swap)."""
        return self._llm.embed([query])[0]

    def retrieve(self, vector: list[float], iteration: int) -> RetrievalResult:
        """Search at the escalation level of ``iteration`` (1 precision, 2 and 3 expansion)."""
        s = self._settings
        if iteration <= 1:
            limit, max_parents = s.top_k_precision, 0
        elif iteration == 2:
            limit, max_parents = s.top_k_level2, s.max_parents_level2
        else:
            limit, max_parents = s.top_k_level3, s.max_parents_level3
        hits = self._store.search(vector, limit)
        best = max((h.score for h in hits), default=0.0)
        passed = bool(hits) and best >= s.min_retrieval_score
        if max_parents:
            parent_ids = ordered_parent_ids(hits, max_parents)
            parents = {p.parent_id: p for p in self._docstore.get_parents(parent_ids)}
            sources = group_parents(hits, parents, parent_ids)
            mode: Literal["precision", "expansion"] = "expansion"
        else:
            sources = group_sources(hits)
            mode = "precision"
        logger.info(
            "retrieve iteration=%d mode=%s hits=%d sources=%d best=%.3f gate=%s (min %.2f)",
            iteration,
            mode,
            len(hits),
            len(sources),
            best,
            "pass" if passed else "FAIL",
            s.min_retrieval_score,
        )
        return RetrievalResult(sources, best, passed, mode)
