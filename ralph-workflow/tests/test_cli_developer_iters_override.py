from __future__ import annotations

from ralph.cli.main import counter_overrides_with_developer_iters

_REQUESTED = 12
_EXPLICIT_COUNTER = 3


def test_developer_iters_flag_becomes_an_iteration_override() -> None:
    assert counter_overrides_with_developer_iters({}, _REQUESTED) == {"iteration": _REQUESTED}


def test_explicit_counter_override_wins_over_the_flag() -> None:
    assert counter_overrides_with_developer_iters({"iteration": _EXPLICIT_COUNTER}, _REQUESTED) == {
        "iteration": _EXPLICIT_COUNTER
    }


def test_unspecified_flag_adds_nothing() -> None:
    assert counter_overrides_with_developer_iters({}, None) == {}


def test_other_counters_are_preserved() -> None:
    assert counter_overrides_with_developer_iters({"reviewer_pass": 2}, _REQUESTED) == {
        "reviewer_pass": 2,
        "iteration": _REQUESTED,
    }
