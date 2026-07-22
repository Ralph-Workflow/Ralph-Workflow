"""Tests for the dirty-phase-boundary mainline refresh.

Covers the throttle itself (fake clock, no sleeping) and the
:func:`ralph.pipeline.auto_integrate` boundary path that consumes it.

The boundary path used to answer "nothing to catch up" from a purely
local ref read, so a mainline another agent had moved minutes earlier
produced a silent, unrecorded no-op. These tests pin that the deferral
is now decided from a freshly observed pointer and that the refresh
outcome is recorded on the state the operator sees.

Two throttle defects are pinned here as well, because both produced
staleness the throttle was supposed to bound:

* it was a process-global scalar keyed on NOTHING, so two workspace
  scopes or two targets in one process stole each other's window;
* it consumed the window on PERMIT rather than on a successful refresh,
  so one unreachable-origin blip guaranteed a whole interval of
  unrefreshed boundary probes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import auto_integrate
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_NO_ORIGIN,
    REFRESH_REFRESHED,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

    from ralph.git.subprocess_runner import GitRunOptions

_TARGET_SHA = "1111111111111111111111111111111111111111"
_HEAD_SHA = "2222222222222222222222222222222222222222"
_ROOT_A = "/workspace/agent-a"
_ROOT_B = "/workspace/agent-b"


class _FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _permit_and_succeed(
    throttle: BoundaryRefreshThrottle, root: str, target: str
) -> bool:
    """Ask for a permit and, when granted, report a SUCCESSFUL refresh.

    The production caller always pairs the two calls; expressing that
    pairing once keeps every test below reading as the sequence the
    boundary path actually performs.
    """
    permitted = throttle.should_refresh(root, target)
    if permitted:
        throttle.record_outcome(root, target, REFRESH_REFRESHED)
    return permitted


def test_first_call_is_always_permitted() -> None:
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=_FakeClock())
    assert throttle.should_refresh(_ROOT_A, "main") is True


def test_second_call_inside_the_interval_is_suppressed() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_A, "main") is False
    clock.advance(29.9)
    assert throttle.should_refresh(_ROOT_A, "main") is False


def test_call_after_the_interval_is_permitted_again() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    clock.advance(30.0)
    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_A, "main") is False


def test_custom_minimum_interval_is_honoured() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=5.0, clock=clock)
    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    clock.advance(4.0)
    assert throttle.should_refresh(_ROOT_A, "main") is False
    clock.advance(1.0)
    assert throttle.should_refresh(_ROOT_A, "main") is True


def test_suppressed_calls_do_not_extend_the_window() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=10.0, clock=clock)
    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    for _ in range(5):
        clock.advance(1.0)
        assert throttle.should_refresh(_ROOT_A, "main") is False
    # 10 s after the SUCCESSFUL refresh, not 10 s after the last attempt.
    clock.advance(5.0)
    assert throttle.should_refresh(_ROOT_A, "main") is True


def test_independent_instances_do_not_share_state() -> None:
    clock = _FakeClock()
    first = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    second = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    assert _permit_and_succeed(first, _ROOT_A, "main") is True
    assert first.should_refresh(_ROOT_A, "main") is False
    assert second.should_refresh(_ROOT_A, "main") is True


def test_each_repository_root_gets_its_own_window() -> None:
    """Two worktrees in one process must not steal each other's refresh.

    The throttle used to hold ONE ``_last_refresh`` keyed on nothing, so
    whichever scope probed first consumed the whole interval for every
    other scope in the process -- the exact topology (a fleet of linked
    worktrees, or several manifest-launched parallel workers) that
    auto-integration exists to keep in sync.
    """
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)

    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_A, "main") is False
    assert throttle.should_refresh(_ROOT_B, "main") is True


def test_each_target_branch_gets_its_own_window() -> None:
    """The same defect keyed on the target: two targets, two windows."""
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)

    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_A, "main") is False
    assert throttle.should_refresh(_ROOT_A, "develop") is True


def test_an_unsuccessful_refresh_does_not_consume_the_window() -> None:
    """A blip must not blind the next whole interval.

    The window used to be armed the moment permission was GRANTED, so a
    refresh that came back ``origin unreachable`` -- establishing no
    freshness whatsoever -- still bought thirty seconds of silence. The
    window is now armed only by an outcome that can vouch for the
    pointer.
    """
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)

    assert throttle.should_refresh(_ROOT_A, "main") is True
    throttle.record_outcome(_ROOT_A, "main", REFRESH_UNREACHABLE)

    assert throttle.should_refresh(_ROOT_A, "main") is True, (
        "a refresh that established no freshness must not suppress the next probe"
    )
    throttle.record_outcome(_ROOT_A, "main", REFRESH_REFRESHED)
    assert throttle.should_refresh(_ROOT_A, "main") is False


def test_tracked_windows_are_capped_and_evicted_oldest_first() -> None:
    """The window map is a long-lived accumulator, so it carries a cap."""
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(
        min_interval_seconds=30.0, clock=clock, max_tracked_keys=2
    )

    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert _permit_and_succeed(throttle, _ROOT_B, "main") is True
    # A third key evicts the oldest, so its window is forgotten and the
    # map never grows past the cap across a long unattended run.
    assert _permit_and_succeed(throttle, "/workspace/agent-c", "main") is True

    assert throttle.should_refresh(_ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_B, "main") is False


# ---------------------------------------------------------------------------
# The boundary path that consumes the throttle
# ---------------------------------------------------------------------------


def _dirty_boundary_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> WorkspaceScope:
    """A workspace whose phase-boundary pre-checks reach the dirty deferral.

    The target carries a commit this checkout lacks, which is the case
    the deferral must RECORD rather than swallow.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(auto_integrate, "_worktree_is_clean", lambda root: False)
    monkeypatch.setattr(
        auto_integrate, "resolve_integration_target", lambda config, root: "main"
    )
    monkeypatch.setattr(auto_integrate, "branch_sha", lambda root, ref: _TARGET_SHA)
    monkeypatch.setattr(auto_integrate, "get_head_sha", lambda root: _HEAD_SHA)
    monkeypatch.setattr(
        auto_integrate, "is_ancestor", lambda root, ancestor, descendant: False
    )
    monkeypatch.setattr(
        auto_integrate,
        "BOUNDARY_REFRESH_THROTTLE",
        BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=_FakeClock()),
    )
    return WorkspaceScope(tmp_path)


def _config() -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"}
    }
    return UnifiedConfig.model_validate(payload)


def test_dirty_boundary_records_the_refresh_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A recorded dirty-boundary skip carries how its pointer was read."""
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    refresh_calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_REFRESHED

    monkeypatch.setattr(auto_integrate, "_refresh_target", _fake_refresh)

    outcome = auto_integrate.auto_integrate_on_phase_transition(
        _config(), scope, RebaseState()
    )

    assert refresh_calls == ["main"], "the boundary must observe a fresh pointer"
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_REFRESHED


def test_dirty_boundary_without_an_origin_records_no_origin_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A linked-worktree fleet has no origin; that must be recorded, not hidden.

    The local ref IS the authoritative pointer in that topology, so the
    absent fetch is correct by construction -- but the operator still
    has to be able to see why no fetch happened.
    """
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)

    def _fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
    ) -> GitRunResult:
        # 'remote get-url origin' fails => no origin remote configured.
        return GitRunResult(args=tuple(args), returncode=2, stdout="", stderr="")

    monkeypatch.setattr("ralph.pipeline.auto_integrate_sync.run_git", _fake_run_git)

    outcome = auto_integrate.auto_integrate_on_phase_transition(
        _config(), scope, RebaseState()
    )

    assert outcome is not None
    assert outcome.last_refresh == REFRESH_NO_ORIGIN


def test_throttled_boundary_records_the_suppression_without_refetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The eleven boundary events of one cycle collapse to a single fetch.

    Defect this pins (it lived at ``_defer_dirty_boundary``, which
    passed ``None`` when the throttle declined): the suppressed round
    recorded a skip with ``last_refresh=None``, which
    ``refresh_outcome_is_healthy`` then classified as HEALTHY. A
    boundary decided from a pointer nobody re-read is exactly as
    unverifiable as one whose refresh failed, so the suppression is now
    named on the record instead of being absent from it.
    """
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    refresh_calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_REFRESHED

    monkeypatch.setattr(auto_integrate, "_refresh_target", _fake_refresh)
    config = _config()

    first = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )
    second = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )

    assert len(refresh_calls) == 1, "the throttle must suppress the second fetch"
    assert first is not None
    assert first.last_refresh == REFRESH_REFRESHED
    assert second is not None
    assert second.last_action == "skipped"
    assert second.last_refresh == REFRESH_SUPPRESSED


def test_a_failed_boundary_refresh_is_retried_on_the_next_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One unreachable-origin blip must not blind the next boundary probe."""
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    refresh_calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_UNREACHABLE

    monkeypatch.setattr(auto_integrate, "_refresh_target", _fake_refresh)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )

    assert refresh_calls == ["main", "main"], (
        "a refresh that could not reach origin must not consume the window"
    )
    assert second is not None
    assert second.last_refresh == REFRESH_UNREACHABLE
