"""Regression tests for integration timing seams and rebase failure reasons.

The integration retry back-off must run on the clock installed for the
attempt, and a rebase failure caused by the environment must never be
presented to the operator as a content conflict.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase._rebase_kind import RebaseKind
from ralph.git.rebase.rebase import RebaseFailed
from ralph.git.rebase.rebase_kinds import RebaseErrorKind
from ralph.pipeline import auto_integrate as auto_integrate_module
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_rebase_merge import run_rebase_or_merge
from ralph.pipeline.conflict_resolution import driver as conflict_driver
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._fake_git_env import SimulatedGitEnvironment
from tests._fake_git_ops import FakeGitOps

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession


class _RecordingClock:
    """Clock double that records waits without waiting on real time."""

    def __init__(self) -> None:
        self.current = 1000.0
        self.slept: deque[float] = deque(maxlen=16)

    def now(self) -> float:
        """Return the fake clock's current time."""
        return self.current

    def sleep(self, seconds: float) -> None:
        """Record and apply a wait without blocking the test process."""
        self.slept.append(seconds)
        self.current += seconds


class _EnvironmentalGitOps(FakeGitOps):
    """Git-ops double that returns one classified rebase failure."""

    def __init__(self, result: RebaseFailed) -> None:
        super().__init__(SimulatedGitEnvironment())
        self.result = result

    def rebase_onto(self, root: Path, target: str) -> RebaseFailed:
        """Return the configured environmental failure."""
        return self.result


class _NoAgentRunner:
    """Agent-runner double that fails if a resolver is dispatched."""

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        """Reject any resolver dispatch."""
        raise AssertionError("an environmental rebase failure must not dispatch an agent")


def _config() -> UnifiedConfig:
    """Return a local-only integration configuration."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def _stub_integration_observations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Replace repository observations with deterministic local facts."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        auto_integrate_module,
        "resolve_integration_target",
        lambda *_args, **_kwargs: "main",
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "_current_branch_or_detached_marker",
        lambda *_args, **_kwargs: "feature",
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "_refresh_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "_reattach_head_if_detached",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "containment_release",
        lambda _root, _target, state: state,
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "_auto_integrate_check_skip_conditions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "observe_conflict_identity",
        lambda *_args, **_kwargs: ConflictIdentity(),
    )
    monkeypatch.setattr(
        auto_integrate_module,
        "_recover_before_attempt",
        lambda *_args, **_kwargs: None,
    )


def test_integration_clock_regression_installed_clock_records_the_backoff_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production retry back-off waits on the clock installed for the attempt."""
    _stub_integration_observations(monkeypatch, tmp_path)
    clock = _RecordingClock()
    attempts = 0

    def _one_retry_then_land(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[RebaseState, bool]:
        nonlocal attempts
        attempts += 1
        return (
            RebaseState(
                last_outcome=RebaseOutcomeKind.SKIPPED,
                last_reason="target advanced concurrently",
                last_target="main",
            ),
            attempts == 1,
        )

    monkeypatch.setattr(auto_integrate_module, "_integrate_once", _one_retry_then_land)
    monkeypatch.setattr(auto_integrate_module._random, "random", lambda: 0.0)
    production_wait = auto_integrate_module.wait_before_retry
    selected_sleeps: list[Callable[[float], None]] = []

    def _checked_wait(
        attempt: int,
        *,
        sleep: Callable[[float], None],
        jitter: Callable[[], float],
    ) -> None:
        selected_sleeps.append(sleep)
        if sleep == clock.sleep:
            production_wait(attempt, sleep=sleep, jitter=jitter)

    monkeypatch.setattr(auto_integrate_module, "wait_before_retry", _checked_wait)
    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert selected_sleeps == [clock.sleep]
    assert list(clock.slept) == [0.25]
    assert clock.now() == 1000.25


@pytest.mark.parametrize(
    ("result", "expected_reason"),
    [
        (
            RebaseFailed(
                RebaseErrorKind(
                    kind=RebaseKind.ENVIRONMENT_FAILURE,
                    metadata={"reason": "author identity is not configured"},
                )
            ),
            (
                "rebase failed for an environmental reason (environment_failure): "
                "author identity is not configured"
            ),
        ),
        (
            RebaseFailed(
                RebaseErrorKind(
                    kind=RebaseKind.UNKNOWN,
                    metadata={"details": "git rebase timed out after 120.0s"},
                )
            ),
            (
                "rebase failed for an environmental reason (timeout): "
                "git rebase timed out after 120.0s"
            ),
        ),
        (
            RebaseFailed(
                RebaseErrorKind(
                    kind=RebaseKind.UNKNOWN,
                    metadata={"details": "git rebase was killed mid-write"},
                )
            ),
            ("rebase failed for an environmental reason (killed): git rebase was killed mid-write"),
        ),
        (
            RebaseFailed(
                RebaseErrorKind(
                    kind=RebaseKind.REPOSITORY_CORRUPT,
                    metadata={"details": "no space left on device"},
                )
            ),
            (
                "rebase failed for an environmental reason (repository_corrupt): "
                "no space left on device"
            ),
        ),
    ],
    ids=("environment", "timeout", "killed", "io"),
)
def test_integration_rebase_regression_environmental_failure_is_not_a_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: RebaseFailed,
    expected_reason: str,
) -> None:
    """An environmental rebase failure is reported accurately and never merged."""
    git_ops = _EnvironmentalGitOps(result)
    endpoint_calls: list[object] = []

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge._range_routing_reason",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )

    def _endpoint_merge(*_args: object, **_kwargs: object) -> None:
        endpoint_calls.append("endpoint merge")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.endpoint_merge_with_resolution",
        _endpoint_merge,
    )

    outcome = run_rebase_or_merge(
        tmp_path,
        "main",
        None,
        git_ops=git_ops,
        agent_runner=_NoAgentRunner(),
    )

    assert endpoint_calls == []
    assert outcome.merge_attempted is False
    assert outcome.merge_outcome is None
    assert outcome.short_circuit is not None
    assert outcome.short_circuit.last_outcome == RebaseOutcomeKind.SKIPPED
    assert outcome.short_circuit.last_reason == expected_reason
    assert "conflict" not in (outcome.short_circuit.last_reason or "").lower()


def test_conflict_resolution_regression_sleep_seconds_accepts_an_injected_sleep() -> None:
    """The conflict-resolution wait seam never requires a real sleep in tests."""
    waits: list[float] = []

    conflict_driver._sleep_seconds(0.25, sleep=waits.append)

    assert waits == [0.25]
