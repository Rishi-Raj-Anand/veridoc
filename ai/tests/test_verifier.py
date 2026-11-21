"""Verifier: strict-JSON contract and the pass rule computed in code (CLAUDE.md §6.5)."""

from __future__ import annotations

import pytest

from ai_service.agents.verifier import Verifier, VerifierOutput, passes
from ai_service.config import Settings
from ai_service.retrieval.retriever import Source
from tests.helpers import FakeLlm, verifier_json

SETTINGS = Settings(_env_file=None)  # thresholds 0.85 / 0.75
SOURCES = [Source(n=1, filename="disease.md", page=1, text="Use bed nets.", score=0.9)]


def output(**overrides: object) -> VerifierOutput:
    base: dict[str, object] = {
        "faithfulness": 1.0,
        "relevance": 1.0,
        "unsupported_claims": [],
        "missing_info": None,
        "verdict": "pass",
    }
    return VerifierOutput.model_validate({**base, **overrides})


# --- pass-rule truth table -------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"faithfulness": 0.85, "relevance": 0.75}, True),  # exactly at both thresholds
        ({"faithfulness": 0.8499}, False),
        ({"relevance": 0.7499}, False),
        ({"verdict": "fail"}, False),  # perfect scores, but the model said fail
        ({"unsupported_claims": ["claims a dose"]}, False),  # model said pass, claims remain
        ({"verdict": "fail", "faithfulness": 0.1, "relevance": 0.1}, False),
    ],
)
def test_the_pass_rule_needs_every_condition(overrides: dict[str, object], expected: bool) -> None:
    assert passes(output(**overrides), SETTINGS) is expected


def test_the_thresholds_come_from_settings() -> None:
    strict = Settings(_env_file=None, faithfulness_threshold=0.95)

    assert passes(output(faithfulness=0.9), SETTINGS)
    assert not passes(output(faithfulness=0.9), strict)


# --- the Verifier agent ----------------------------------------------------------------


def verify(llm: FakeLlm, draft: str = "Use bed nets [1]."):
    return Verifier(llm, SETTINGS).verify("How is malaria prevented?", SOURCES, draft)


def test_a_passing_reply_passes() -> None:
    result = verify(FakeLlm(verifier_replies=[verifier_json()]))

    assert result.passed
    assert (result.faithfulness, result.relevance) == (1.0, 1.0)
    assert not result.parse_failed


def test_a_pass_verdict_with_low_faithfulness_fails() -> None:
    result = verify(FakeLlm(verifier_replies=[verifier_json(faithfulness=0.5, verdict="pass")]))

    assert not result.passed


def test_missing_info_and_claims_are_returned_and_tidied() -> None:
    reply = verifier_json(
        faithfulness=0.4,
        unsupported=["dose of 5 mg", "  "],
        missing="  malaria in children ",
        verdict="fail",
    )

    result = verify(FakeLlm(verifier_replies=[reply]))

    assert result.unsupported_claims == ("dose of 5 mg",)
    assert result.missing_info == "malaria in children"


def test_a_blank_missing_info_becomes_none() -> None:
    result = verify(FakeLlm(verifier_replies=[verifier_json(missing="   ")]))

    assert result.missing_info is None


def test_the_call_is_deterministic_structured_and_sees_only_the_context() -> None:
    llm = FakeLlm(verifier_replies=[verifier_json()])

    verify(llm)

    call = llm.chat_calls[0]
    assert call["temperature"] == 0
    assert call["format"] == VerifierOutput.model_json_schema()
    system, user = (m["content"] for m in call["messages"])
    assert "auditor" in system
    assert "[1] disease.md, p. 1\nUse bed nets." in user
    assert "Question: How is malaria prevented?" in user
    assert "Draft answer:\nUse bed nets [1]." in user
    assert len(call["messages"]) == 2  # system + user: no chat history


def test_malformed_json_is_retried_once_and_the_retry_can_succeed() -> None:
    llm = FakeLlm(verifier_replies=["not json", verifier_json()])

    result = verify(llm)

    assert result.passed
    assert len(llm.chat_calls) == 2


def test_malformed_json_twice_is_a_failure_after_exactly_two_calls() -> None:
    llm = FakeLlm(verifier_replies=["not json", "{still not"])

    result = verify(llm)

    assert not result.passed
    assert result.parse_failed
    assert len(llm.chat_calls) == 2


@pytest.mark.parametrize(
    "reply",
    [
        verifier_json(faithfulness=1.5),
        verifier_json(relevance=-0.1),
        '{"faithfulness": 1.0, "relevance": 1.0}',  # fields missing
        '{"faithfulness": 1.0, "relevance": 1.0, "unsupported_claims": [], '
        '"missing_info": null, "verdict": "maybe"}',
    ],
)
def test_a_reply_that_breaks_the_schema_counts_as_malformed(reply: str) -> None:
    result = verify(FakeLlm(verifier_replies=[reply]))

    assert result.parse_failed
    assert not result.passed


def test_a_think_block_around_the_json_is_ignored() -> None:
    result = verify(FakeLlm(verifier_replies=["<think>hmm</think>" + verifier_json()]))

    assert result.passed
