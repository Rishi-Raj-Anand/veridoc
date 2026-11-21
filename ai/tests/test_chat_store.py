"""Transcript store: round-trip, atomic writes, ownership, corruption (CLAUDE.md §6.7)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_service.config import EMERGENCY_NOTICE
from ai_service.memory import chat_store
from ai_service.memory.chat_store import ChatStore, SessionOwnershipError, TurnEntry

SESSION = "33333333-3333-4333-8333-333333333333"


def user(text: str = "What is migraine?") -> TurnEntry:
    return TurnEntry(role="user", content=text, timestamp="2026-11-21T10:00:00+00:00")


def assistant(text: str = "A headache disorder [1].", **extra: object) -> TurnEntry:
    fields: dict = {
        "standalone_query": "What is migraine?",
        "status": "verified",
        "emergency": False,
        "iterations": 1,
        "citations": [{"n": 1, "filename": "m.md", "page": 1}],
        "verifier_scores": [{"iteration": 1, "outcome": "pass", "faithfulness": 1.0}],
    }
    fields.update(extra)
    return TurnEntry(
        role="assistant", content=text, timestamp="2026-11-21T10:00:05+00:00", **fields
    )


def test_an_unknown_session_has_no_transcript(tmp_path: Path) -> None:
    assert ChatStore(tmp_path).load(SESSION, 1) is None


def test_an_exchange_round_trips_with_the_section_6_7_fields(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)

    store.append_exchange(SESSION, 5, user(), assistant())

    raw = json.loads((tmp_path / f"{SESSION}.json").read_text(encoding="utf-8"))
    assert set(raw) == {"session_id", "user_id", "created_at", "turns"}
    assert (raw["session_id"], raw["user_id"]) == (SESSION, 5)
    first, second = raw["turns"]
    assert first["role"] == "user"
    assert second["standalone_query"] == "What is migraine?"
    assert second["status"] == "verified"
    assert second["iterations"] == 1
    assert second["citations"] == [{"n": 1, "filename": "m.md", "page": 1}]
    assert second["verifier_scores"][0]["faithfulness"] == 1.0
    assert store.load(SESSION, 5) is not None


def test_a_second_exchange_is_appended(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    store.append_exchange(SESSION, 5, user("one"), assistant("a"))

    store.append_exchange(SESSION, 5, user("two"), assistant("b"))

    transcript = store.load(SESSION, 5)
    assert transcript is not None
    assert [t.content for t in transcript.turns] == ["one", "a", "two", "b"]


def test_no_temp_file_is_left_behind(tmp_path: Path) -> None:
    ChatStore(tmp_path).append_exchange(SESSION, 5, user(), assistant())

    assert [p.name for p in tmp_path.iterdir()] == [f"{SESSION}.json"]


def test_a_failed_replace_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ChatStore(tmp_path)
    store.append_exchange(SESSION, 5, user("one"), assistant("a"))
    before = (tmp_path / f"{SESSION}.json").read_text(encoding="utf-8")
    monkeypatch.setattr(chat_store.time, "sleep", lambda _s: None)

    def deny(_src: object, _dst: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(chat_store.os, "replace", deny)

    with pytest.raises(PermissionError):
        store.append_exchange(SESSION, 5, user("two"), assistant("b"))

    assert (tmp_path / f"{SESSION}.json").read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == [f"{SESSION}.json"]


def test_a_brief_lock_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("briefly locked")
        real_replace(src, dst)

    monkeypatch.setattr(chat_store.os, "replace", flaky)
    monkeypatch.setattr(chat_store.time, "sleep", lambda _s: None)

    ChatStore(tmp_path).append_exchange(SESSION, 5, user(), assistant())

    assert calls["n"] == 2
    assert (tmp_path / f"{SESSION}.json").exists()


def test_a_corrupt_file_is_quarantined_and_the_session_starts_fresh(tmp_path: Path) -> None:
    (tmp_path / f"{SESSION}.json").write_text("{not json", encoding="utf-8")
    store = ChatStore(tmp_path)

    assert store.load(SESSION, 5) is None

    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 1 and names[0].startswith(f"{SESSION}.json.corrupt-")
    store.append_exchange(SESSION, 5, user(), assistant())
    assert store.load(SESSION, 5) is not None


def test_another_users_session_is_refused(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    store.append_exchange(SESSION, 5, user(), assistant())

    with pytest.raises(SessionOwnershipError):
        store.load(SESSION, 6)


@pytest.mark.parametrize("bad", ["../etc/passwd", "not-a-uuid", "", "..\\x"])
def test_a_session_id_that_is_not_a_uuid_is_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        ChatStore(tmp_path).load(bad, 1)


def test_the_emergency_notice_survives_a_round_trip_and_is_stripped_from_history(
    tmp_path: Path,
) -> None:
    store = ChatStore(tmp_path)
    store.append_exchange(
        SESSION,
        5,
        user("chest pain"),
        assistant(f"{EMERGENCY_NOTICE}\n\nRest [1].", emergency=True),
    )

    transcript = store.load(SESSION, 5)

    assert transcript is not None
    assert transcript.turns[1].content.startswith("⚠️")
    history = store.history(transcript, 6)
    assert history[1] == {"role": "assistant", "content": "Rest [1]."}


def test_history_is_the_last_n_messages_as_plain_dicts(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    for i in range(4):
        store.append_exchange(SESSION, 5, user(f"q{i}"), assistant(f"a{i}"))
    transcript = store.load(SESSION, 5)

    history = store.history(transcript, 3)

    assert history == [
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    assert store.history(None, 6) == []
    assert store.history(transcript, 0) == []
