"""The single-pass graph and its guards (mock LLM)."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from ai_service.agents.generator import Generator
from ai_service.config import FALLBACK_MESSAGE, Settings
from ai_service.graph.pipeline import build_pipeline, run_turn
from ai_service.ingestion.chunker import ChildChunk
from ai_service.retrieval.retriever import Retriever
from ai_service.retrieval.vector_store import VectorStore
from tests.helpers import FakeLlm

DIM = 1024
SESSION = "11111111-1111-4111-8111-111111111111"


def v(*head: float) -> list[float]:
    return list(head) + [0.0] * (DIM - len(head))


def make(
    reply: str, *, chunk_vector: list[float] | None = None
) -> tuple[FakeLlm, VectorStore, Settings]:
    settings = Settings(_env_file=None)
    store = VectorStore(QdrantClient(":memory:"), settings)
    store.ensure_collection()
    children = [
        ChildChunk("1:p00000", 0, 1, "Malaria is prevented with nets."),
        ChildChunk("1:p00000", 1, 2, "Cold: rest and fluids."),
    ]
    store.upsert_children(1, "disease.md", children, [chunk_vector or v(1.0), v(0.9, 0.4)])
    return FakeLlm(query_vector=v(1.0), reply=reply), store, settings


def run(llm: FakeLlm, store: VectorStore, settings: Settings):
    pipeline = build_pipeline(Retriever(store, llm, settings), Generator(llm, settings))
    return run_turn(pipeline, session_id=SESSION, user_id=7, message="How is malaria prevented?")


def test_grounded_answer_is_verified_with_only_the_cited_sources() -> None:
    llm, store, settings = make("Use bed nets [1].")

    result = run(llm, store, settings)

    assert result.status == "verified"
    assert result.answer == "Use bed nets [1]."
    assert [(c["n"], c["filename"], c["page"]) for c in result.citations] == [(1, "disease.md", 1)]
    assert not result.emergency


def test_the_generator_runs_at_temperature_point_two_with_the_context() -> None:
    llm, store, settings = make("Use bed nets [1].")

    run(llm, store, settings)

    call = llm.chat_calls[0]
    assert call["temperature"] == 0.2
    user = call["messages"][1]["content"]
    assert "[1] disease.md, p. 1" in user
    assert "Question: How is malaria prevented?" in user


def test_out_of_range_markers_are_stripped_but_a_valid_one_still_passes() -> None:
    llm, store, settings = make("Use bed nets [1]. Also magic [9].")

    result = run(llm, store, settings)

    assert result.status == "verified"
    assert "[9]" not in result.answer
    assert [c["n"] for c in result.citations] == [1]


def test_low_retrieval_score_falls_back_without_calling_the_llm() -> None:
    llm, store, settings = make("never used", chunk_vector=v(0.0, 1.0))
    llm.query_vector = v(1.0)
    store.delete_document(1)
    store.upsert_children(1, "x.md", [ChildChunk("1:p0", 0, 1, "off topic")], [v(0.0, 1.0)])

    result = run(llm, store, settings)

    assert result.status == "fallback"
    assert result.answer == FALLBACK_MESSAGE
    assert result.citations == []
    assert llm.chat_calls == []


def test_an_empty_knowledge_base_falls_back_without_calling_the_llm() -> None:
    llm, store, settings = make("never used")
    store.delete_document(1)

    result = run(llm, store, settings)

    assert result.status == "fallback"
    assert llm.chat_calls == []


@pytest.mark.parametrize("reply", ["NOT_IN_DOCUMENTS", "", "<think></think>"])
def test_the_not_covered_sentinel_or_an_empty_draft_falls_back(reply: str) -> None:
    llm, store, settings = make(reply)

    result = run(llm, store, settings)

    assert (result.status, result.answer) == ("fallback", FALLBACK_MESSAGE)
    assert len(llm.chat_calls) == 1


@pytest.mark.parametrize("reply", ["Bed nets work.", "Bed nets work [5]."])
def test_a_draft_without_a_valid_citation_falls_back(reply: str) -> None:
    llm, store, settings = make(reply)

    result = run(llm, store, settings)

    assert (result.status, result.answer, result.citations) == ("fallback", FALLBACK_MESSAGE, [])
