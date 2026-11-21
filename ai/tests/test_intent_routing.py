"""classify_intent and its three branches inside the pipeline (CLAUDE.md §6.9)."""

from __future__ import annotations

from pathlib import Path

from ai_service.agents.admin_text import ADMIN_HEADING
from ai_service.agents.smalltalk import DEFAULT_REPLY, MAX_REPLY_CHARS, clip
from ai_service.behaviour import DEFAULT_OUT_OF_SCOPE_MESSAGE, BehaviourSettings
from ai_service.config import EMERGENCY_NOTICE
from tests.helpers import FakeLlm, intent_json
from tests.test_pipeline import Env, v


def llm(**kwargs: object) -> FakeLlm:
    return FakeLlm(query_vector=v(1.0), reply="Use bed nets [1].", **kwargs)


def test_a_plain_greeting_is_answered_without_the_classifier_or_retrieval(tmp_path: Path) -> None:
    env = Env(tmp_path, llm(smalltalk_replies=["Hi! I'm Doctor AI. How can I help?"]))

    result = env.run("hi")

    assert (result.intent, result.status) == ("smalltalk", "smalltalk")
    assert result.answer == "Hi! I'm Doctor AI. How can I help?"
    assert result.citations == []
    assert env.llm.events == ["chat:smalltalk"]  # one LLM call, no embedding, no verifier


def test_chit_chat_the_classifier_calls_small_talk_is_answered(tmp_path: Path) -> None:
    env = Env(tmp_path, llm(classify_replies=[intent_json("smalltalk")]))

    result = env.run("you are a nice bot, I like talking to you")

    assert result.status == "smalltalk"
    assert env.llm.events == ["chat:classify", "chat:smalltalk"]


def test_an_off_topic_request_gets_the_refusal_without_any_generation(tmp_path: Path) -> None:
    env = Env(tmp_path, llm(classify_replies=[intent_json("out_of_scope")]))

    result = env.run("Give me a recipe for pasta")

    assert (result.intent, result.status) == ("out_of_scope", "out_of_scope")
    assert result.answer == DEFAULT_OUT_OF_SCOPE_MESSAGE
    assert result.citations == []
    assert env.llm.events == ["chat:classify"]


def test_the_refusal_text_follows_the_admin_setting_on_the_next_message(tmp_path: Path) -> None:
    env = Env(tmp_path, llm(classify_replies=[intent_json("out_of_scope")]))
    env.behaviour.update(
        BehaviourSettings(out_of_scope_message="Only health questions here, sorry!"), "now"
    )

    assert env.run("fix my laptop").answer == "Only health questions here, sorry!"


def test_the_scope_setting_reaches_the_classifier(tmp_path: Path) -> None:
    env = Env(tmp_path, llm())
    env.behaviour.update(BehaviourSettings(scope="Dermatology only."), "now")

    env.run("How is malaria prevented?")

    assert (
        "In-scope topics: Dermatology only." in env.llm.classify_calls[0]["messages"][1]["content"]
    )


def test_a_medical_question_still_runs_the_verified_loop(tmp_path: Path) -> None:
    env = Env(tmp_path, llm())

    result = env.run()

    assert (result.intent, result.status) == ("medical", "verified")
    assert env.llm.events == ["chat:classify", "embed", "chat:gen", "chat:verify"]


def test_an_emergency_is_never_classified_and_never_small_talk(tmp_path: Path) -> None:
    # Even a classifier that would say "smalltalk" is never asked.
    env = Env(tmp_path, llm(classify_replies=[intent_json("smalltalk")]))

    result = env.run("hi, I have chest pain")

    assert result.intent == "medical"
    assert env.llm.classify_calls == []
    assert result.answer.startswith(EMERGENCY_NOTICE)


def test_a_greeting_with_a_question_goes_to_the_classifier(tmp_path: Path) -> None:
    env = Env(tmp_path, llm())

    result = env.run("hi, how is malaria prevented?")

    assert len(env.llm.classify_calls) == 1
    assert result.status == "verified"


def test_small_talk_and_refusals_are_saved_with_their_intent(tmp_path: Path) -> None:
    env = Env(tmp_path, llm(classify_replies=[intent_json("out_of_scope")]))

    env.run("hello")
    env.run("book me a flight")

    transcript = env.chats.load("11111111-1111-4111-8111-111111111111", 7)
    assert transcript is not None
    assistant = [t for t in transcript.turns if t.role == "assistant"]
    assert [(t.intent, t.status) for t in assistant] == [
        ("smalltalk", "smalltalk"),
        ("out_of_scope", "out_of_scope"),
    ]


def test_admin_text_goes_to_the_generator_and_small_talk_but_never_the_verifier(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path, llm())
    env.behaviour.update(
        BehaviourSettings(persona="Speak like a kind nurse.", extra_guardrails="Never use slang."),
        "now",
    )

    env.run()
    env.run("hello")

    generator_system = env.llm.generator_calls[0]["messages"][0]["content"]
    assert generator_system.index(ADMIN_HEADING) > generator_system.index("Rules:")
    assert "Speak like a kind nurse." in generator_system
    assert "Never use slang." in generator_system
    assert "Never use slang." in env.llm.smalltalk_calls[0]["messages"][0]["content"]
    for call in env.llm.verifier_calls + env.llm.classify_calls:
        assert all("Never use slang." not in m["content"] for m in call["messages"])


def test_an_empty_small_talk_reply_falls_back_to_a_fixed_greeting(tmp_path: Path) -> None:
    env = Env(tmp_path, llm(smalltalk_replies=["   "]))

    assert env.run("hey").answer == DEFAULT_REPLY


def test_an_over_long_small_talk_reply_is_clipped_at_a_sentence() -> None:
    text = "Hello there. " * 100

    clipped = clip(text)

    assert len(clipped) <= MAX_REPLY_CHARS
    assert clipped.endswith(".")
