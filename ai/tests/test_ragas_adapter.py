"""The RAGAS judge adapter over OllamaClient (CLAUDE.md §6.6, §13).

Skipped unless the optional eval group is installed (``uv sync --group eval``).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import pytest

pytest.importorskip("ragas")

from langchain_core.prompt_values import StringPromptValue

from ai_service.eval.ragas_adapter import (
    OllamaRagasEmbeddings,
    OllamaRagasLLM,
    judge_run_config,
)
from ai_service.eval.run_ragas import RunRow, build_metrics, score_rows

QUESTION = "What are the symptoms of the common cold?"
ANSWER = "The common cold causes a runny nose [1]."


class ScriptedJudge:
    """An ``OllamaClient`` stand-in that answers each RAGAS prompt kind with valid JSON."""

    def __init__(self) -> None:
        self.chats: list[dict[str, Any]] = []
        self.embeds: list[list[str]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float, **_: Any) -> str:
        prompt = messages[0]["content"]
        self.chats.append({"prompt": prompt, "temperature": temperature})
        if prompt.startswith("Given a question and an answer, analyze"):
            return json.dumps({"statements": ["The common cold causes a runny nose."]})
        if prompt.startswith("Your task is to judge the faithfulness"):
            return json.dumps(
                {
                    "statements": [
                        {
                            "statement": "The common cold causes a runny nose.",
                            "reason": "stated in the context",
                            "verdict": 1,
                        }
                    ]
                }
            )
        if prompt.startswith("Given question, answer and context verify"):
            return json.dumps({"reason": "useful", "verdict": 1})
        if prompt.startswith("Generate a question for the given answer"):
            return json.dumps({"question": QUESTION, "noncommittal": 0})
        raise AssertionError(f"unexpected RAGAS prompt: {prompt[:80]}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embeds.append(list(texts))
        return [_vector(t) for t in texts]


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 for b in digest]


def test_one_completion_runs_at_temperature_zero() -> None:
    judge = ScriptedJudge()
    llm = OllamaRagasLLM(judge)  # type: ignore[arg-type]

    result = llm.generate_text(StringPromptValue(text="Given question, answer and context verify"))

    assert len(result.generations[0]) == 1
    assert judge.chats[0]["temperature"] == 0.0
    assert llm.is_finished(result)


async def test_n_completions_are_n_calls_at_the_requested_temperature() -> None:
    judge = ScriptedJudge()
    llm = OllamaRagasLLM(judge)  # type: ignore[arg-type]

    result = await llm.agenerate_text(
        StringPromptValue(text="Generate a question for the given answer"), n=3, temperature=0.3
    )

    assert len(result.generations[0]) == 3
    assert [c["temperature"] for c in judge.chats] == [0.3, 0.3, 0.3]


async def test_embeddings_are_delegated() -> None:
    judge = ScriptedJudge()
    embeddings = OllamaRagasEmbeddings(judge)  # type: ignore[arg-type]

    assert embeddings.embed_query("a") == _vector("a")
    assert await embeddings.aembed_documents(["a", "b"]) == [_vector("a"), _vector("b")]
    assert judge.embeds == [["a"], ["a", "b"]]


def test_the_judge_runs_one_call_at_a_time() -> None:
    assert judge_run_config().max_workers == 1


def test_every_metric_gets_the_local_judge_never_a_default() -> None:
    judge = ScriptedJudge()
    llm, emb = OllamaRagasLLM(judge), OllamaRagasEmbeddings(judge)  # type: ignore[arg-type]

    metrics = build_metrics(["faithfulness", "answer_relevancy", "context_precision"], llm, emb)

    assert [m.name for m in metrics] == [
        "faithfulness",
        "answer_relevancy",
        "llm_context_precision_with_reference",
    ]
    assert all(m.llm is llm for m in metrics)
    assert metrics[1].embeddings is emb


def test_telemetry_is_off() -> None:
    from ragas._analytics import do_not_track

    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
    assert do_not_track()


def test_score_rows_fills_all_three_metrics_through_ragas_evaluate() -> None:
    judge = ScriptedJudge()
    row = RunRow(
        variant="full",
        id="cold",
        question=QUESTION,
        reference="A runny nose.",
        source="disease.md",
        expect="answer",
        intent="medical",
        status="verified",
        answer=ANSWER,
        contexts=["Common cold: runny nose, sneezing, sore throat."],
    )
    refused = row.model_copy(update={"id": "x", "status": "fallback"}, deep=True)

    score_rows([row, refused], judge)  # type: ignore[arg-type]

    assert row.scores["faithfulness"] == 1.0
    assert row.scores["context_precision"] == pytest.approx(1.0, abs=1e-6)
    assert row.scores["answer_relevancy"] == pytest.approx(1.0, abs=1e-6)
    assert refused.scores == {}
    assert judge.embeds  # Response Relevancy used our embeddings, not a default provider


@pytest.mark.ollama
def test_live_judge_separates_a_faithful_answer_from_a_contradicting_one() -> None:
    """Real qwen3:8b + bge-m3 through OllamaClient: the judge must tell the two apart."""
    from ai_service.config import get_settings
    from ai_service.llm.ollama_client import OllamaClient

    context = "Common cold: runny or stuffy nose, sneezing, sore throat, mild cough."
    base = {
        "variant": "full",
        "question": QUESTION,
        "reference": "Runny or stuffy nose, sneezing, sore throat and a mild cough.",
        "source": "disease.md",
        "expect": "answer",
        "intent": "medical",
        "status": "verified",
        "contexts": [context],
    }
    faithful = RunRow(id="good", answer="It causes a runny nose and sneezing [1].", **base)
    wrong = RunRow(id="bad", answer="It causes hair loss and broken bones [1].", **base)

    score_rows([faithful, wrong], OllamaClient(get_settings()))

    assert faithful.scores["faithfulness"] is not None
    assert faithful.scores["faithfulness"] > 0.5
    assert wrong.scores["faithfulness"] is not None
    assert wrong.scores["faithfulness"] < 0.5
