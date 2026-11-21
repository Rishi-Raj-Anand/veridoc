"""Settings guards."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_service.config import Settings


@pytest.mark.parametrize("value", [1, 2, 3])
def test_max_iterations_accepts_the_three_defined_levels(value: int) -> None:
    assert Settings(_env_file=None, max_iterations=value).max_iterations == value


@pytest.mark.parametrize("value", [0, 4, -1])
def test_max_iterations_outside_one_to_three_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError, match="MAX_ITERATIONS"):
        Settings(_env_file=None, max_iterations=value)


def test_escalation_defaults_match_the_spec() -> None:
    s = Settings(_env_file=None)

    assert (s.top_k_precision, s.top_k_level2, s.top_k_level3) == (3, 8, 15)
    assert (s.max_parents_level2, s.max_parents_level3) == (4, 6)
    assert (s.context_max_tokens, s.history_max_tokens) == (4800, 1500)
