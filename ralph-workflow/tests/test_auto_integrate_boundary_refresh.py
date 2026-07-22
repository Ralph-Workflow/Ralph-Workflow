"""Tests for the dirty-phase-boundary mainline refresh.

Covers the throttle itself (fake clock, no sleeping) and the
:func:`ralph.pipeline.auto_integrate` boundary path that consumes it.

The boundary path used to answer "nothing to catch up" from a purely
local ref read, so a mainline another agent had moved minutes earlier
produced a silent, unrecorded no-op. These tests pin that the deferral
is now decided from a freshly observed pointer and that the refresh
outcome is recorded on the state the operator sees.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import auto_integrate
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_sync import REFRESH_NO_ORIGIN, REFRESH_REFRESHED
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

    from ralph.git.subprocess_runner import GitRunOptions

_TARGET_SHA = "1111111111111111111111111111111111111111"
_HEAD_SHA = "2222222222222222222222222222222222222222"


class _FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_first_call_is_always_permitted() -> None:
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=_FakeClock())
    assert throttle.should_refresh() is True


def test_second_call_inside_the_interval_is_suppressed() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    assert throttle.should_refresh() is True
    assert throttle.should_refresh() is False
    clock.advance(29.9)
    assert throttle.should_refresh() is False


def test_call_after_the_interval_is_permitted_again() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    assert throttle.should_refresh() is True
    clock.advance(30.0)
    assert throttle.should_refresh() is True
    assert throttle.should_refresh() is False


def test_custom_minimum_interval_is_honoured() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=5.0, clock=clock)
    assert throttle.should_refresh() is True
    clock.advance(4.0)
    assert throttle.should_refresh() is False
    clock.advance(1.0)
    assert throttle.should_refresh() is True


def test_suppressed_calls_do_not_extend_the_window() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=10.0, clock=clock)
    assert throttle.should_refresh() is True
    for _ in range(5):
        clock.advance(1.0)
        assert throttle.should_refresh() is False
    # 10 s after the PERMITTED refresh, not 10 s after the last attempt.
    clock.advance(5.0)
    assert throttle.should_refresh() is True


def test_independent_instances_do_not_share_state() -> None:
    clock = _FakeClock()
    first = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    second = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)
    assert first.should_refresh() is True
    assert first.should_refresh() is False
    assert second.should_refresh() is True


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


def test_throttled_boundary_still_records_the_skip_without_refetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The eleven boundary events of one cycle collapse to a single fetch."""
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
    # The suppressed round still reports the lost catch-up; it simply
    # carries no freshness claim it did not establish.
    assert second is not None
    assert second.last_action == "skipped"
    assert second.last_refresh is None
