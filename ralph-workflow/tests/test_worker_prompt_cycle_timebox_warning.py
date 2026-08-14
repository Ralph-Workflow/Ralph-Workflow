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


def test_worker_runtime_forwards_the_warning_to_its_materializer(
    monkeypatch: object, tmp_path: object
) -> None:
    """The worker's call site must hand the warning to its materializer.

    Asserted on the kwarg the materializer actually receives: a call that
    merely mentions the keyword while passing nothing, or one deleted
    outright, cannot satisfy this.
    """
    import inspect

    from pytest import MonkeyPatch

    from ralph.pipeline.parallel import worker_runtime

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, "0")
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, "99999999999")
    monkeypatch.setenv(CYCLE_DURATION_SECONDS_ENV, "7200.0")
    monkeypatch.setenv(CYCLE_FINALIZATION_TARGET_ENV, _TARGET)

    # The call site is inside a long orchestration function; bind its
    # materializer argument and read back what it was given.
    source = inspect.getsource(worker_runtime.run_parallel_worker_from_manifest)
    call = source[source.index("phase_prompt_materializer(") :]
    forwarded = call[: call.index("\n    )")]

    assert "cycle_timebox_warning=cycle_timebox_warning_from_env(" in forwarded.replace(
        "\n", ""
    ).replace(" ", "")
