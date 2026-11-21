"""LangGraph state for one chat turn (CLAUDE.md §6.2)."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from ai_service.agents.verifier import VerifierResult
from ai_service.retrieval.retriever import Source


class CitationDict(TypedDict):
    n: int
    filename: str
    page: int


class Attempt(TypedDict, total=False):
    """One pass through the loop, kept for logging, transcripts (5.1) and evaluation (7.x)."""

    iteration: int
    mode: Literal["precision", "expansion"]
    best_score: float
    sources: int
    outcome: Literal["low_score", "not_covered", "uncited", "pass", "fail"]
    faithfulness: float
    relevance: float
    parse_failed: bool


class PipelineState(TypedDict, total=False):
    # input
    session_id: str
    user_id: int
    message: str
    # working
    query: str  # the standalone question; Generator and Verifier always see this
    retrieval_query: str  # query plus the latest missing_info; what gets embedded
    query_vector: list[float]
    vector_query: str  # the text query_vector was computed from (lets us skip re-embedding)
    iteration: int
    retrieval_mode: Literal["precision", "expansion"]
    sources: list[Source]
    best_score: float
    gate_passed: bool
    draft: str  # citation-cleaned, the exact text finalize would return
    draft_outcome: Literal["ok", "not_covered", "uncited"]
    verification: VerifierResult | None
    attempts: Annotated[list[Attempt], operator.add]
    # output
    answer: str
    citations: list[CitationDict]
    status: Literal["verified", "fallback"]
    emergency: bool
