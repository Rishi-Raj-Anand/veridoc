"""The query pipeline as a LangGraph state machine (CLAUDE.md §6.2).

::

    retrieve -> generate -> verify -> finalize
        ^                      |
        '---- escalate <-------'   (fail and iteration < MAX_ITERATIONS)
                                   fail on the last iteration -> fallback

``generate`` and ``verify`` are adjacent so no embedding call can fall between them and
evict the LLM (§6.8). Every path that cannot produce a verified answer ends in ``fallback``;
an unverified draft is never returned.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai_service.agents.budget import trim_sources
from ai_service.agents.citations import clean_citations, is_not_covered
from ai_service.agents.generator import Generator
from ai_service.agents.verifier import Verifier
from ai_service.config import FALLBACK_MESSAGE, Settings
from ai_service.graph.state import Attempt, CitationDict, PipelineState
from ai_service.retrieval.retriever import Retriever, Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    answer: str
    citations: list[CitationDict]
    status: Literal["verified", "fallback"]
    emergency: bool
    iterations: int
    attempts: list[Attempt]
    sources: list[Source]


def build_pipeline(
    retriever: Retriever, generator: Generator, verifier: Verifier, settings: Settings
) -> CompiledStateGraph:
    max_iterations = settings.max_iterations

    def gate_route(state: PipelineState) -> Literal["generate", "escalate", "fallback"]:
        """§6.3: a low best score skips iteration 1; iteration 2 lets the Verifier judge; the
        last iteration falls back, and so does a retrieval that found nothing at all."""
        if state["gate_passed"]:
            return "generate"
        if not state["sources"]:  # empty knowledge base: nothing to ground an answer on
            return "fallback"
        if state["iteration"] >= max_iterations:
            return "fallback"
        if state["iteration"] == 1:
            return "escalate"
        return "generate"

    def after_failure(state: PipelineState) -> Literal["escalate", "fallback"]:
        return "escalate" if state["iteration"] < max_iterations else "fallback"

    def retrieve(state: PipelineState) -> PipelineState:
        # The condenser (task 5.2) will rewrite follow-ups here; for now the query is the message.
        query = state.get("query") or state["message"]
        retrieval_query = state.get("retrieval_query") or query
        iteration = state.get("iteration", 1)
        vector = state.get("query_vector")
        if vector is None or state.get("vector_query") != retrieval_query:
            vector = retriever.embed_query(retrieval_query)
        result = retriever.retrieve(vector, iteration)
        sources = trim_sources(result.sources, settings.context_max_tokens)
        update: PipelineState = {
            "query": query,
            "retrieval_query": retrieval_query,
            "query_vector": vector,
            "vector_query": retrieval_query,
            "iteration": iteration,
            "retrieval_mode": result.mode,
            "sources": sources,
            "best_score": result.best_score,
            "gate_passed": result.passed_gate,
            "verification": None,
        }
        probe: PipelineState = {**state, **update}
        if gate_route(probe) != "generate":
            update["attempts"] = [
                {
                    "iteration": iteration,
                    "mode": result.mode,
                    "best_score": result.best_score,
                    "sources": len(sources),
                    "outcome": "low_score",
                }
            ]
        elif not result.passed_gate:
            logger.info(
                "iteration %d: best score under the gate, letting the Verifier judge", iteration
            )
        return update

    def generate(state: PipelineState) -> PipelineState:
        raw = generator.generate(state["query"], state["sources"])
        valid = {s.n for s in state["sources"]}
        draft, used = clean_citations(raw, valid)
        outcome: Literal["ok", "not_covered", "uncited"] = "ok"
        if not draft or is_not_covered(raw):
            outcome = "not_covered"
        elif not used:
            outcome = "uncited"
        update: PipelineState = {"draft": draft, "draft_outcome": outcome}
        if outcome != "ok":
            logger.info(
                "iteration %d: draft is %s, skipping the Verifier", state["iteration"], outcome
            )
            update["attempts"] = [
                {
                    "iteration": state["iteration"],
                    "mode": state["retrieval_mode"],
                    "best_score": state["best_score"],
                    "sources": len(state["sources"]),
                    "outcome": outcome,
                }
            ]
        return update

    def after_generate(state: PipelineState) -> Literal["verify", "escalate", "fallback"]:
        if state["draft_outcome"] == "ok":
            return "verify"
        return after_failure(state)

    def verify(state: PipelineState) -> PipelineState:
        result = verifier.verify(state["query"], state["sources"], state["draft"])
        attempt: Attempt = {
            "iteration": state["iteration"],
            "mode": state["retrieval_mode"],
            "best_score": state["best_score"],
            "sources": len(state["sources"]),
            "outcome": "pass" if result.passed else "fail",
            "faithfulness": result.faithfulness,
            "relevance": result.relevance,
            "parse_failed": result.parse_failed,
        }
        return {"verification": result, "attempts": [attempt]}

    def after_verify(state: PipelineState) -> Literal["finalize", "escalate", "fallback"]:
        verification = state["verification"]
        if verification is not None and verification.passed:
            return "finalize"
        return after_failure(state)

    def escalate(state: PipelineState) -> PipelineState:
        verification = state.get("verification")
        missing = verification.missing_info if verification else None
        update: PipelineState = {"iteration": state["iteration"] + 1}
        if missing:
            update["retrieval_query"] = f"{state['query']} {missing}"
        return update

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
    graph.add_node("verify", verify)
    graph.add_node("escalate", escalate)
    graph.add_node("finalize", finalize)
    graph.add_node("fallback", fallback)
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", gate_route, ["generate", "escalate", "fallback"])
    graph.add_conditional_edges("generate", after_generate, ["verify", "escalate", "fallback"])
    graph.add_conditional_edges("verify", after_verify, ["finalize", "escalate", "fallback"])
    graph.add_edge("escalate", "retrieve")
    graph.add_edge("finalize", END)
    graph.add_edge("fallback", END)
    return graph.compile()


def _attempt_summary(attempt: Attempt) -> str:
    text = f"i{attempt['iteration']}:{attempt['outcome']}@{attempt['best_score']:.2f}"
    if "faithfulness" in attempt:
        text += f"(f={attempt['faithfulness']:.2f},r={attempt['relevance']:.2f})"
    return text


def run_turn(
    pipeline: CompiledStateGraph, *, session_id: str, user_id: int, message: str
) -> PipelineResult:
    """Run one chat turn through the graph (used by the router and, later, the eval script)."""
    started = time.perf_counter()
    state = pipeline.invoke({"session_id": session_id, "user_id": user_id, "message": message})
    attempts = state.get("attempts", [])
    result = PipelineResult(
        answer=state["answer"],
        citations=state["citations"],
        status=state["status"],
        emergency=state.get("emergency", False),
        iterations=state.get("iteration", 0),
        attempts=attempts,
        sources=state.get("sources", []),
    )
    logger.info(
        "chat turn session=%s user=%d status=%s iterations=%d attempts=[%s] citations=%d "
        "latency=%.2fs",
        session_id,
        user_id,
        result.status,
        result.iterations,
        " ".join(_attempt_summary(a) for a in attempts),
        len(result.citations),
        time.perf_counter() - started,
    )
    return result
