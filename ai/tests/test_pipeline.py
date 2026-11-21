"""The verify/escalate loop, its guards and the fallback (mock LLM, CLAUDE.md §6.2-6.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from ai_service.agents.generator import Generator
from ai_service.agents.verifier import Verifier
from ai_service.config import FALLBACK_MESSAGE, Settings
from ai_service.graph.pipeline import PipelineResult, build_pipeline, run_turn
from ai_service.ingestion.chunker import ChildChunk, ParentChunk
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.retriever import Retriever
from ai_service.retrieval.vector_store import VectorStore
from tests.helpers import FakeLlm, verifier_json

DIM = 1024
SESSION = "11111111-1111-4111-8111-111111111111"
QUESTION = "How is malaria prevented?"
PARENT_ONLY_FACT = "Treatment is artemisinin combination therapy."

FAIL = verifier_json(faithfulness=0.3, verdict="fail", unsupported=["a claim"])
FAIL_MISSING = verifier_json(faithfulness=0.3, verdict="fail", missing="malaria treatment")
PASS = verifier_json()


def v(*head: float) -> list[float]:
    return list(head) + [0.0] * (DIM - len(head))


class Env:
    """A tiny knowledge base: one parent whose second half is not in any retrieved child."""

    def __init__(self, tmp_path: Path, llm: FakeLlm, *, low_score: bool = False, **cfg: int):
        self.settings = Settings(_env_file=None, **cfg)
        self.llm = llm
        self.store = VectorStore(QdrantClient(":memory:"), self.settings)
        self.store.ensure_collection()
        self.docstore = Docstore(tmp_path / "docstore.db")
        # Query vector is v(1.0). A low-score chunk is orthogonal to it (cosine 0.0).
        near = v(0.0, 1.0) if low_score else v(1.0)
        self.store.upsert_children(
            1,
            "disease.md",
            [
                ChildChunk("1:p00000", 0, 1, "Malaria is prevented with nets."),
                ChildChunk("1:p00001", 1, 2, "Cold: rest and fluids."),
            ],
            [near, v(0.9, 0.4) if not low_score else v(0.0, 0.9, 0.4)],
        )
        self.docstore.replace_document(
            1,
            "disease.md",
            [
                ParentChunk(
                    "1:p00000", 0, 1, 1, f"Malaria is prevented with nets. {PARENT_ONLY_FACT}"
                ),
                ParentChunk("1:p00001", 1, 2, 2, "Cold: rest and fluids."),
            ],
        )
        self.pipeline = build_pipeline(
            Retriever(self.store, self.docstore, llm, self.settings),
            Generator(llm, self.settings),
            Verifier(llm, self.settings),
            self.settings,
        )

    def run(self, message: str = QUESTION) -> PipelineResult:
        return run_turn(self.pipeline, session_id=SESSION, user_id=7, message=message)


def outcomes(result: PipelineResult) -> list[str]:
    return [a["outcome"] for a in result.attempts]


# --- the happy path --------------------------------------------------------------------


def test_a_draft_the_verifier_passes_is_verified_at_iteration_one(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    result = env.run()

    assert (result.status, result.answer) == ("verified", "Use bed nets [1].")
    assert [(c["n"], c["filename"], c["page"]) for c in result.citations] == [(1, "disease.md", 1)]
    assert result.iterations == 1
    assert outcomes(result) == ["pass"]
    assert not result.emergency


def test_the_generator_runs_at_temperature_point_two_with_the_context(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    env.run()

    call = env.llm.generator_calls[0]
    assert call["temperature"] == 0.2
    user = call["messages"][1]["content"]
    assert "[1] disease.md, p. 1" in user
    assert f"Question: {QUESTION}" in user


def test_the_verifier_audits_the_same_context_and_the_cleaned_draft(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]. Magic [9]."))

    result = env.run()

    gen_user = env.llm.generator_calls[0]["messages"][1]["content"]
    ver_user = env.llm.verifier_calls[0]["messages"][1]["content"]
    assert gen_user.split("\n\nQuestion:")[0] == ver_user.split("\n\nQuestion:")[0]
    assert ver_user.endswith("Draft answer:\nUse bed nets [1]. Magic.")  # [9] already stripped
    assert result.answer == "Use bed nets [1]. Magic."  # the audited text is the returned text


# --- escalation ------------------------------------------------------------------------


def test_a_failed_audit_escalates_to_the_parent_chunk_and_can_then_pass(tmp_path: Path) -> None:
    llm = FakeLlm(
        query_vector=v(1.0),
        replies=["Use bed nets [1].", f"Use bed nets. {PARENT_ONLY_FACT} [1]"],
        verifier_replies=[FAIL_MISSING, PASS],
    )
    env = Env(tmp_path, llm)

    result = env.run()

    assert (result.status, result.iterations) == ("verified", 2)
    assert outcomes(result) == ["fail", "pass"]
    first, second = (c["messages"][1]["content"] for c in llm.generator_calls)
    assert PARENT_ONLY_FACT not in first  # iteration 1 sees only the child chunk
    assert PARENT_ONLY_FACT in second  # iteration 2 reaches it through the parent
    assert [a["mode"] for a in result.attempts] == ["precision", "expansion"]


def test_the_escalated_query_appends_the_latest_missing_info(tmp_path: Path) -> None:
    llm = FakeLlm(
        query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL_MISSING, PASS]
    )

    Env(tmp_path, llm).run()

    assert llm.calls == [[QUESTION], [f"{QUESTION} malaria treatment"]]
    # The Generator and Verifier still see the original question only.
    assert "malaria treatment" not in llm.generator_calls[1]["messages"][1]["content"]
    assert "malaria treatment" not in llm.verifier_calls[1]["messages"][1]["content"]


def test_an_unchanged_query_is_not_embedded_again(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL, PASS])

    Env(tmp_path, llm).run()  # missing_info is null, so the retrieval query never changes

    assert llm.calls == [[QUESTION]]


def test_no_embedding_call_ever_falls_between_generate_and_verify(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL_MISSING])

    Env(tmp_path, llm).run()

    events = llm.events
    assert events.count("chat:gen") == 3
    for i, event in enumerate(events):
        if event == "chat:gen":
            assert events[i + 1] == "chat:verify"


# --- the fallback ----------------------------------------------------------------------


def test_three_failed_audits_return_the_exact_fallback_and_never_a_fourth_pass(
    tmp_path: Path,
) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL])

    result = Env(tmp_path, llm).run()

    assert (result.status, result.answer, result.citations) == ("fallback", FALLBACK_MESSAGE, [])
    assert result.iterations == 3
    assert outcomes(result) == ["fail", "fail", "fail"]
    assert len(llm.generator_calls) == 3
    assert len(llm.verifier_calls) == 3


def test_a_pass_verdict_that_misses_a_threshold_still_fails(tmp_path: Path) -> None:
    llm = FakeLlm(
        query_vector=v(1.0),
        reply="Use bed nets [1].",
        verifier_replies=[verifier_json(faithfulness=0.6, verdict="pass")],
    )

    result = Env(tmp_path, llm).run()

    assert result.status == "fallback"


def test_malformed_verifier_json_every_time_ends_in_the_fallback(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=["not json"])

    result = Env(tmp_path, llm).run()

    assert result.status == "fallback"
    assert all(a["parse_failed"] for a in result.attempts)
    assert len(llm.generator_calls) == 3
    assert len(llm.verifier_calls) == 6  # one retry per iteration


def test_max_iterations_one_gives_a_single_attempt(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL])

    result = Env(tmp_path, llm, max_iterations=1).run()

    assert (result.status, result.iterations) == ("fallback", 1)
    assert len(llm.chat_calls) == 2


# --- drafts that are never audited -----------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["NOT_IN_DOCUMENTS", "", "<think></think>", "Nets work.", "Nets [5]."]
)
def test_an_uncovered_or_uncited_draft_escalates_without_calling_the_verifier(
    tmp_path: Path, bad: str
) -> None:
    llm = FakeLlm(query_vector=v(1.0), replies=[bad, "Use bed nets [1]."])

    result = Env(tmp_path, llm).run()

    assert (result.status, result.iterations) == ("verified", 2)
    assert outcomes(result)[1] == "pass"
    assert outcomes(result)[0] in {"not_covered", "uncited"}
    assert len(llm.verifier_calls) == 1


def test_a_draft_that_is_never_covered_falls_back_after_three_generations(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="NOT_IN_DOCUMENTS")

    result = Env(tmp_path, llm).run()

    assert (result.status, result.answer) == ("fallback", FALLBACK_MESSAGE)
    assert outcomes(result) == ["not_covered"] * 3
    assert len(llm.generator_calls) == 3
    assert llm.verifier_calls == []


# --- the retrieval-score gate (§6.3) ---------------------------------------------------


def test_a_low_score_skips_iteration_one_and_the_verifier_judges_iteration_two(
    tmp_path: Path,
) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].")

    result = Env(tmp_path, llm, low_score=True).run()

    assert (result.status, result.iterations) == ("verified", 2)
    assert outcomes(result) == ["low_score", "pass"]
    assert len(llm.generator_calls) == 1  # nothing was generated at iteration 1
    assert llm.calls == [[QUESTION]]  # and the unchanged query was embedded once


def test_a_score_still_low_at_the_last_iteration_falls_back_without_another_llm_call(
    tmp_path: Path,
) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL])

    result = Env(tmp_path, llm, low_score=True).run()

    assert (result.status, result.iterations) == ("fallback", 3)
    assert outcomes(result) == ["low_score", "fail", "low_score"]
    assert len(llm.chat_calls) == 2  # one generation and one audit, at iteration 2 only


def test_an_empty_knowledge_base_falls_back_without_calling_the_llm(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="never used")
    env = Env(tmp_path, llm)
    env.store.delete_document(1)

    result = env.run()

    assert (result.status, result.answer) == ("fallback", FALLBACK_MESSAGE)
    assert llm.chat_calls == []


def test_the_iteration_count_never_exceeds_the_cap(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL_MISSING])

    result = Env(tmp_path, llm).run()

    assert result.iterations <= 3
    assert max(a["iteration"] for a in result.attempts) == 3
