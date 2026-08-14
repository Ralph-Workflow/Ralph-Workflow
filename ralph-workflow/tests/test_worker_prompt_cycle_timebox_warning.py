"""Cycle-timebox warning on a parallel worker's prompt.

A fan-out worker runs in its own process from a manifest, so its pipeline
state carries no cycle timing at all — the deadline reaches it only through
the environment its parent published. Without this the worker could spend the
tail of the cycle budget with no idea a deadline was approaching, while the
serial development agent doing the same work was warned.
"""

from __future__ import annotations

from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_DURATION_SECONDS_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
)
from ralph.pipeline.prompt_prep import cycle_timebox_warning_from_env

_NOW = 1_000_000.0
_TARGET = "development_final_commit_cleanup"


def _published(*, warn_in: float, deadline_in: float) -> dict[str, str]:
    return {
        CYCLE_WARN_EPOCH_ENV: repr(_NOW + warn_in),
        CYCLE_DEADLINE_EPOCH_ENV: repr(_NOW + deadline_in),
        CYCLE_DURATION_SECONDS_ENV: repr(7200.0),
        CYCLE_FINALIZATION_TARGET_ENV: _TARGET,
    }


def test_worker_is_warned_once_the_published_warning_point_has_passed() -> None:
    warning = cycle_timebox_warning_from_env(
        _published(warn_in=-60.0, deadline_in=1380.0), now_epoch=_NOW
    )

    assert warning is not None
    assert warning["remaining_seconds"] == 1380.0
    assert warning["elapsed_seconds"] == 7200.0 - 1380.0
    assert warning["finalization_target"] == _TARGET


def test_worker_is_not_warned_before_the_warning_point() -> None:
    assert (
        cycle_timebox_warning_from_env(
            _published(warn_in=60.0, deadline_in=1500.0), now_epoch=_NOW
        )
        is None
    )


def test_worker_is_not_warned_when_no_deadline_was_published() -> None:
    assert cycle_timebox_warning_from_env({}, now_epoch=_NOW) is None


def test_worker_warning_ignores_unusable_published_values() -> None:
    unusable = {
        CYCLE_WARN_EPOCH_ENV: "nan",
        CYCLE_DEADLINE_EPOCH_ENV: "later",
        CYCLE_DURATION_SECONDS_ENV: "",
        CYCLE_FINALIZATION_TARGET_ENV: _TARGET,
    }

    assert cycle_timebox_warning_from_env(unusable, now_epoch=_NOW) is None


def test_worker_runtime_passes_the_warning_to_its_materializer() -> None:
    """The worker's own call site must forward it, not just the helper exist."""
    import inspect

    from ralph.pipeline.parallel import worker_runtime

    source = inspect.getsource(worker_runtime.run_parallel_worker_from_manifest)
    assert "cycle_timebox_warning" in source
