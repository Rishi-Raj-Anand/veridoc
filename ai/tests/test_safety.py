"""safety_check terms (CLAUDE.md §6.2, §7.2): every term matches, benign text does not."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_service.agents.safety import SafetyChecker

CHECKER = SafetyChecker.load()
CATEGORIES = {c.name: c for c in CHECKER.categories}
EXAMPLES = [(c.name, phrase) for c in CHECKER.categories for phrase in c.examples]

REQUIRED = {
    "chest_pain",
    "breathing",
    "stroke",
    "severe_bleeding",
    "self_harm",
    "poisoning_overdose",
    "unconscious",
}


def test_every_red_flag_category_from_the_spec_is_present() -> None:
    assert set(CATEGORIES) >= REQUIRED


@pytest.mark.parametrize(("category", "phrase"), EXAMPLES)
def test_every_example_matches_its_own_category(category: str, phrase: str) -> None:
    assert category in CHECKER.check(phrase)


@pytest.mark.parametrize(("category", "phrase"), EXAMPLES)
def test_matching_is_case_insensitive(category: str, phrase: str) -> None:
    assert category in CHECKER.check(phrase.upper())


def test_every_pattern_is_exercised_by_an_example() -> None:
    for category in CHECKER.categories:
        for pattern in category.patterns:
            assert any(pattern.search(e) for e in category.examples), pattern.pattern


@pytest.mark.parametrize(
    "text",
    [
        "I have a mild headache",
        "How is malaria prevented?",
        "What is the treatment for migraine in children?",
        "Is paracetamol safe with food?",
        "what should I eat for a common cold",
        "My chestnut allergy is mild",
    ],
)
def test_benign_questions_do_not_match(text: str) -> None:
    assert CHECKER.check(text) == []


def test_a_message_can_match_several_categories() -> None:
    matches = CHECKER.check("crushing chest pain and I can't breathe")

    assert {"chest_pain", "breathing"} <= set(matches)


def test_a_negated_mention_still_matches_by_design() -> None:
    assert CHECKER.check("I have no chest pain") == ["chest_pain"]


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "terms.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_missing_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SafetyChecker.load(tmp_path / "absent.yaml")


def test_an_invalid_regex_fails_at_load(tmp_path: Path) -> None:
    path = _write(tmp_path, "categories:\n  - name: x\n    patterns: ['(unclosed']\n")

    with pytest.raises(ValueError, match="invalid regex"):
        SafetyChecker.load(path)


@pytest.mark.parametrize(
    "text",
    ["categories: []\n", "nothing: here\n", "categories:\n  - name: x\n    patterns: []\n"],
)
def test_an_empty_or_malformed_file_fails_at_load(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValueError):
        SafetyChecker.load(_write(tmp_path, text))
