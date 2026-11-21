"""Prompt-budget trimming (CLAUDE.md §6.4)."""

from __future__ import annotations

from ai_service.agents.budget import trim_history, trim_sources
from ai_service.retrieval.retriever import Source, format_context


def source(n: int, chars: int, score: float) -> Source:
    return Source(n=n, filename="a.md", page=n, text="x" * chars, score=score)


def test_sources_that_fit_are_left_alone() -> None:
    sources = [source(1, 400, 0.9), source(2, 400, 0.8)]

    assert trim_sources(sources, max_tokens=4800) == sources


def test_the_lowest_scoring_source_is_dropped_first_and_numbering_stays_contiguous() -> None:
    sources = [source(1, 400, 0.9), source(2, 400, 0.8), source(3, 400, 0.7)]

    kept = trim_sources(sources, max_tokens=230)

    assert [s.n for s in kept] == [1, 2]


def test_one_oversized_source_is_truncated_never_removed() -> None:
    kept = trim_sources([source(1, 8000, 0.9)], max_tokens=500)

    assert len(kept) == 1
    assert 0 < len(kept[0].text) < 8000
    assert len(format_context(kept)) <= 500 * 4


def test_no_sources_is_fine() -> None:
    assert trim_sources([], 100) == []


def msg(role: str, chars: int) -> dict[str, str]:
    return {"role": role, "content": "y" * chars}


def test_history_keeps_the_newest_messages_up_to_the_count_limit() -> None:
    history = [msg("user", 40) for _ in range(10)]

    assert trim_history(history, max_tokens=1500, max_messages=6) == history[-6:]


def test_history_drops_the_oldest_messages_to_fit_the_token_budget() -> None:
    history = [msg("user", 400), msg("assistant", 400), msg("user", 400)]  # 100 tokens each

    kept = trim_history(history, max_tokens=250, max_messages=6)

    assert kept == history[1:]


def test_history_can_be_dropped_entirely() -> None:
    assert trim_history([msg("user", 400)], max_tokens=10, max_messages=6) == []
    assert trim_history([msg("user", 4)], max_tokens=1500, max_messages=0) == []
