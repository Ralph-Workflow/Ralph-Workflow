from __future__ import annotations

from ralph.pipeline.no_progress_hold import (
    NO_PROGRESS_BASE_WAIT_SECONDS,
    NO_PROGRESS_MAX_WAIT_SECONDS,
    NO_PROGRESS_REPEATS,
    NoProgressHoldBackoff,
)

_SAME = ("owned reintegration cannot release its unfinished target landing", "f484d7d", "153a44e")


def test_an_unchanged_retained_failure_backs_off_instead_of_spinning() -> None:
    backoff = NoProgressHoldBackoff()

    waits = [backoff.observe(_SAME) for _ in range(NO_PROGRESS_REPEATS + 6)]

    assert waits[: NO_PROGRESS_REPEATS - 1] == [0.0] * (NO_PROGRESS_REPEATS - 1)
    assert waits[NO_PROGRESS_REPEATS - 1] == NO_PROGRESS_BASE_WAIT_SECONDS
    assert waits[NO_PROGRESS_REPEATS:] == sorted(waits[NO_PROGRESS_REPEATS:])
    assert waits[-1] == NO_PROGRESS_MAX_WAIT_SECONDS
    assert all(wait <= NO_PROGRESS_MAX_WAIT_SECONDS for wait in waits)


def test_a_changed_tree_or_target_restarts_the_streak() -> None:
    backoff = NoProgressHoldBackoff()
    for _ in range(NO_PROGRESS_REPEATS):
        backoff.observe(_SAME)

    assert backoff.observe((_SAME[0], "new-tree", _SAME[2])) == 0.0
    backoff.reset()
    assert backoff.observe(_SAME) == 0.0
