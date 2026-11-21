"""LangGraph state for one chat turn (CLAUDE.md §6.2). Phase 4 adds the Verifier fields."""

from __future__ import annotations

from typing import Literal, TypedDict

from ai_service.retrieval.retriever import Source


class CitationDict(TypedDict):
    n: int
    filename: str
    page: int


class PipelineState(TypedDict, total=False):
    # input
    session_id: str
    user_id: int
    message: str
    # working
    query: str
    iteration: int
    sources: list[Source]
    best_score: float
    gate_passed: bool
    draft: str
    # output
    answer: str
    citations: list[CitationDict]
    status: Literal["verified", "fallback"]
    emergency: bool
