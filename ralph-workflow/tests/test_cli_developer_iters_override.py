"""`-D` and the depth presets as budget instructions on a resumed run.

A resumed run keeps the checkpoint's budget caps, so a developer-iteration
count given on the command line only reaches it as an explicit counter
override. These pin that translation: the flag is folded in when the operator
actually gave one, an explicit `--counter iteration=N` still wins, and an
unspecified flag changes nothing.
"""

from __future__ import annotations

from ralph.cli.main import counter_overrides_with_developer_iters

_REQUESTED = 12
_EXPLICIT_COUNTER = 3


def test_developer_iters_flag_becomes_an_iteration_override() -> None:
    assert counter_overrides_with_developer_iters({}, _REQUESTED) == {"iteration": _REQUESTED}


def test_explicit_counter_override_wins_over_the_flag() -> None:
    assert counter_overrides_with_developer_iters(
        {"iteration": _EXPLICIT_COUNTER}, _REQUESTED
    ) == {"iteration": _EXPLICIT_COUNTER}


def test_unspecified_flag_adds_nothing() -> None:
    assert counter_overrides_with_developer_iters({}, None) == {}


def test_other_counters_are_preserved() -> None:
    assert counter_overrides_with_developer_iters({"reviewer_pass": 2}, _REQUESTED) == {
        "reviewer_pass": 2,
        "iteration": _REQUESTED,
    }
