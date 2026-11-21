"""The verify/escalate loop, its guards and the fallback (mock LLM, CLAUDE.md §6.2-6.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from ai_service.agents.classifier import IntentClassifier
from ai_service.agents.condenser import Condenser
from ai_service.agents.generator import Generator
from ai_service.agents.safety import SafetyChecker
from ai_service.agents.smalltalk import SmallTalk
from ai_service.agents.verifier import Verifier
from ai_service.behaviour import BehaviourStore
from ai_service.config import EMERGENCY_NOTICE, FALLBACK_MESSAGE, Settings
from ai_service.graph.pipeline import PipelineResult, build_pipeline, run_turn
from ai_service.ingestion.chunker import ChildChunk, ParentChunk
from ai_service.memory.chat_store import ChatStore, SessionOwnershipError
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
        self.behaviour = BehaviourStore(tmp_path / "behaviour.json")
        self.pipeline = build_pipeline(
            Retriever(self.store, self.docstore, llm, self.settings),
            Generator(llm, self.settings, self.behaviour),
            Verifier(llm, self.settings),
            Condenser(llm, self.settings),
            SafetyChecker.load(),
            self.settings,
            classifier=IntentClassifier(llm, self.settings),
            smalltalk=SmallTalk(llm, self.settings, self.behaviour),
            behaviour=self.behaviour,
        )
        self.chats = ChatStore(tmp_path / "chats")

    def run(
        self, message: str = QUESTION, *, store: ChatStore | str | None = "env", user_id: int = 7
    ) -> PipelineResult:
        return run_turn(
            self.pipeline,
            store=self.chats if store == "env" else store,
            session_id=SESSION,
            user_id=user_id,
            message=message,
        )


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
    assert len(llm.rag_chat_calls) == 2


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
    assert len(llm.rag_chat_calls) == 2  # one generation and one audit, at iteration 2 only


def test_an_empty_knowledge_base_falls_back_without_calling_the_llm(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="never used")
    env = Env(tmp_path, llm)
    env.store.delete_document(1)

    result = env.run()

    assert (result.status, result.answer) == ("fallback", FALLBACK_MESSAGE)
    assert llm.rag_chat_calls == []  # only the intent classifier ran; nothing was generated


def test_the_iteration_count_never_exceeds_the_cap(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", verifier_replies=[FAIL_MISSING])

    result = Env(tmp_path, llm).run()

    assert result.iterations <= 3
    assert max(a["iteration"] for a in result.attempts) == 3


# --- session memory (Phase 5) ----------------------------------------------------------

FOLLOW_UP = "what about for children?"
STANDALONE = "How is malaria prevented in children?"


def two_turns(tmp_path: Path, **llm_args: object) -> tuple[Env, PipelineResult]:
    llm = FakeLlm(
        query_vector=v(1.0),
        reply="Use bed nets [1].",
        condense_replies=[STANDALONE],
        **llm_args,  # type: ignore[arg-type]
    )
    env = Env(tmp_path, llm)
    env.run()
    return env, env.run(FOLLOW_UP)


def test_the_first_turn_makes_no_condenser_call(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    result = env.run()

    assert env.llm.condense_calls == []
    assert result.standalone_query == QUESTION
    # The classifier runs first, before any embedding (§6.8: the LLM is still resident).
    assert env.llm.events == ["chat:classify", "embed", "chat:gen", "chat:verify"]


def test_a_follow_up_is_condensed_and_retrieval_uses_the_standalone_question(
    tmp_path: Path,
) -> None:
    env, result = two_turns(tmp_path)

    assert result.standalone_query == STANDALONE
    assert env.llm.calls[-1] == [STANDALONE]  # the second embed call
    assert env.llm.events[4:] == [
        "chat:classify",
        "chat:condense",
        "embed",
        "chat:gen",
        "chat:verify",
    ]


def test_generator_and_verifier_see_the_standalone_question_and_no_history(
    tmp_path: Path,
) -> None:
    env, _ = two_turns(tmp_path)

    gen = env.llm.generator_calls[1]["messages"][1]["content"]
    ver = env.llm.verifier_calls[1]["messages"][1]["content"]
    for text in (gen, ver):
        assert f"Question: {STANDALONE}" in text
        assert FOLLOW_UP not in text
        assert "Conversation:" not in text and "Assistant:" not in text


def test_the_condenser_sees_the_earlier_exchange(tmp_path: Path) -> None:
    env, _ = two_turns(tmp_path)

    user = env.llm.condense_calls[0]["messages"][1]["content"]
    assert f"User: {QUESTION}" in user
    assert "Assistant: Use bed nets [1]." in user
    assert f"Latest message: {FOLLOW_UP}" in user


def test_the_transcript_holds_both_exchanges_with_scores(tmp_path: Path) -> None:
    env, _ = two_turns(tmp_path)

    transcript = env.chats.load(SESSION, 7)

    assert transcript is not None
    assert [t.role for t in transcript.turns] == ["user", "assistant", "user", "assistant"]
    assert [t.content for t in transcript.turns if t.role == "user"] == [QUESTION, FOLLOW_UP]
    second = transcript.turns[3]
    assert second.standalone_query == STANDALONE
    assert (second.status, second.iterations, second.emergency) == ("verified", 1, False)
    assert second.citations == [{"n": 1, "filename": "disease.md", "page": 1}]
    assert second.verifier_scores is not None
    assert second.verifier_scores[0]["outcome"] == "pass"
    assert second.verifier_scores[0]["faithfulness"] == 1.0


def test_without_a_store_nothing_is_read_or_written(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    result = env.run(store=None)

    assert result.status == "verified"
    assert not (tmp_path / "chats").exists()


def test_a_save_failure_does_not_fail_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(env.chats, "append_exchange", boom)

    result = env.run()

    assert (result.status, result.answer) == ("verified", "Use bed nets [1].")


def test_another_users_session_is_refused_before_any_llm_call(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))
    env.run()
    calls_before = len(env.llm.events)

    with pytest.raises(SessionOwnershipError):
        env.run(user_id=8)

    assert len(env.llm.events) == calls_before


# --- emergencies -----------------------------------------------------------------------

CHEST = "I have crushing chest pain, what can I take?"


def test_an_emergency_prefixes_a_verified_answer(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    result = env.run(CHEST)

    assert result.emergency
    assert result.status == "verified"
    assert result.answer == f"{EMERGENCY_NOTICE}\n\nUse bed nets [1]."
    assert result.citations  # the pipeline still ran
    assert "chest_pain" in result.emergency_matches


def test_an_emergency_prefixes_the_fallback_too(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="NOT_IN_DOCUMENTS"))

    result = env.run(CHEST)

    assert (result.status, result.emergency) == ("fallback", True)
    assert result.answer == f"{EMERGENCY_NOTICE}\n\n{FALLBACK_MESSAGE}"
    assert result.citations == []


def test_no_emergency_means_no_notice(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    result = env.run()

    assert not result.emergency
    assert EMERGENCY_NOTICE not in result.answer


def test_the_generator_and_verifier_never_see_the_notice(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeLlm(query_vector=v(1.0), reply="Use bed nets [1]."))

    env.run(CHEST)

    for call in env.llm.chat_calls:
        assert all(EMERGENCY_NOTICE not in m["content"] for m in call["messages"])


def test_an_emergency_that_only_appears_in_the_standalone_question_is_flagged(
    tmp_path: Path,
) -> None:
    llm = FakeLlm(
        query_vector=v(1.0),
        reply="Use bed nets [1].",
        condense_replies=["What should I do for severe chest pain right now?"],
    )
    env = Env(tmp_path, llm)
    first = env.run()

    second = env.run("what should I do now?")

    assert not first.emergency
    assert second.emergency
    assert second.answer.startswith(EMERGENCY_NOTICE)


def test_the_transcript_keeps_the_notice_but_history_strips_it(tmp_path: Path) -> None:
    llm = FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", condense_replies=[STANDALONE])
    env = Env(tmp_path, llm)
    env.run(CHEST)

    env.run(FOLLOW_UP)

    stored = env.chats.load(SESSION, 7)
    assert stored is not None and stored.turns[1].content.startswith(EMERGENCY_NOTICE)
    assert stored.turns[1].emergency is True
    condense_input = llm.condense_calls[0]["messages"][1]["content"]
    assert EMERGENCY_NOTICE not in condense_input
    assert "Assistant: Use bed nets [1]." in condense_input
