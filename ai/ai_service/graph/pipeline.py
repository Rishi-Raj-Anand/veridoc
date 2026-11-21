"""The query pipeline as a LangGraph state machine (CLAUDE.md §6.2).

Phase 3 is the single pass: ``retrieve -> generate -> finalize``. Because no Verifier
exists yet, three code-level guards send the turn to ``fallback`` instead of returning
something ungrounded: no chunk reaches ``MIN_RETRIEVAL_SCORE`` (the LLM is never called),
the Generator says ``NOT_IN_DOCUMENTS``, or the draft cites no real source. Phase 4 inserts
``verify`` and ``escalate`` between these nodes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai_service.agents.citations import clean_citations, is_not_covered
from ai_service.agents.generator import Generator
from ai_service.config import FALLBACK_MESSAGE
from ai_service.graph.state import CitationDict, PipelineState
from ai_service.retrieval.retriever import Retriever

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    answer: str
    citations: list[CitationDict]
    status: Literal["verified", "fallback"]
    emergency: bool


def build_pipeline(retriever: Retriever, generator: Generator) -> CompiledStateGraph:
    def retrieve(state: PipelineState) -> PipelineState:
        # The condenser (task 5.2) will rewrite follow-ups here; for now the query is the message.
        query = state["message"]
        result = retriever.retrieve(query)
        return {
            "query": query,
            "iteration": 1,
            "sources": result.sources,
            "best_score": result.best_score,
            "gate_passed": result.passed_gate,
        }

    def after_retrieve(state: PipelineState) -> Literal["generate", "fallback"]:
        return "generate" if state["gate_passed"] else "fallback"

    def generate(state: PipelineState) -> PipelineState:
        return {"draft": generator.generate(state["query"], state["sources"])}

    def after_generate(state: PipelineState) -> Literal["finalize", "fallback"]:
        draft = state["draft"]
        if not draft or is_not_covered(draft):
            logger.info("guard: generator found nothing in the sources")
            return "fallback"
        valid = {s.n for s in state["sources"]}
        _, used = clean_citations(draft, valid)
        if not used:
            logger.info("guard: draft has no valid citation")
            return "fallback"
        return "finalize"

    def finalize(state: PipelineState) -> PipelineState:
        by_n = {s.n: s for s in state["sources"]}
        answer, used = clean_citations(state["draft"], set(by_n))
        citations: list[CitationDict] = [
            {"n": n, "filename": by_n[n].filename, "page": by_n[n].page} for n in used
        ]
        return {"answer": answer, "citations": citations, "status": "verified"}

    def fallback(_state: PipelineState) -> PipelineState:
        return {"answer": FALLBACK_MESSAGE, "citations": [], "status": "fallback"}

    graph = StateGraph(PipelineState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("finalize", finalize)
    graph.add_node("fallback", fallback)
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", after_retrieve, ["generate", "fallback"])
    graph.add_conditional_edges("generate", after_generate, ["finalize", "fallback"])
    graph.add_edge("finalize", END)
    graph.add_edge("fallback", END)
    return graph.compile()


def run_turn(
    pipeline: CompiledStateGraph, *, session_id: str, user_id: int, message: str
) -> PipelineResult:
    """Run one chat turn through the graph (used by the router and, later, the eval script)."""
    started = time.perf_counter()
    state = pipeline.invoke({"session_id": session_id, "user_id": user_id, "message": message})
    result = PipelineResult(
        answer=state["answer"],
        citations=state["citations"],
        status=state["status"],
        emergency=state.get("emergency", False),
    )
    logger.info(
        "chat turn session=%s user=%d status=%s iterations=%d best_score=%.3f citations=%d "
        "latency=%.2fs",
        session_id,
        user_id,
        result.status,
        state.get("iteration", 0),
        state.get("best_score", 0.0),
        len(result.citations),
        time.perf_counter() - started,
    )
    return result
