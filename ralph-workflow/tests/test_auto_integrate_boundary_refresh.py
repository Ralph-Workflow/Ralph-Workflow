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
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import auto_integrate
from ralph.pipeline import auto_integrate_recovery as recovery
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_NO_ORIGIN,
    REFRESH_REFRESHED,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from ralph.git.subprocess_runner import GitRunOptions

_TARGET_SHA = "1111111111111111111111111111111111111111"
_HEAD_SHA = "2222222222222222222222222222222222222222"
#: Feature tip a crashed integration had already rebased but not landed.
_FEATURE_SHA = "3333333333333333333333333333333333333333"
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

    The target here is already contained in ``HEAD``, which is the
    genuinely cheap case the throttle exists for. A suppressed boundary
    whose target IS ahead takes the divergence override instead -- see
    ``test_a_throttled_boundary_with_a_pending_catch_up_forces_one_refresh``
    -- because that verdict is shown to the operator.
    """
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    # First boundary diverges (so it records a skip), second does not.
    ancestry = iter([False, True, True])
    monkeypatch.setattr(
        auto_integrate,
        "is_ancestor",
        lambda root, ancestor, descendant: next(ancestry),
    )
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
    assert second is None, "a contained target is the quiet, fetch-free path"


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


# ---------------------------------------------------------------------------
# The forced refresh: a suppressed pointer must never decide a catch-up
# verdict the operator will be shown.
# ---------------------------------------------------------------------------


def _recording_refresh(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> list[str]:
    """Install a counting ``_refresh_target`` returning ``outcome``."""
    calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        calls.append(target)
        return outcome

    monkeypatch.setattr(auto_integrate, "_refresh_target", _fake_refresh)
    return calls


def test_a_throttled_boundary_with_a_pending_catch_up_forces_one_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The throttle may not decide a divergence verdict it never verified.

    The window is consumed by the first boundary, so the second one is
    suppressed -- but its LOCAL pointer already shows the target ahead,
    which is the one case where the answer is about to be recorded for
    the operator. That verdict must come from a re-read pointer.
    """
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )

    assert len(calls) == 2, "the suppressed boundary must force one refresh"
    assert second is not None
    assert second.last_action == "skipped"
    assert second.last_refresh == REFRESH_REFRESHED, (
        "the recorded verdict must carry the real refresh outcome, "
        "not REFRESH_SUPPRESSED"
    )


def test_a_throttled_boundary_with_nothing_pending_still_costs_no_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The common dirty boundary must stay free; only divergence pays."""
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    # Target already contained in HEAD: nothing to catch up.
    monkeypatch.setattr(
        auto_integrate, "is_ancestor", lambda root, ancestor, descendant: True
    )
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    first = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )
    second = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )

    assert first is None
    assert second is None
    assert len(calls) == 1, "only the throttled first boundary may fetch"


def test_a_forced_refresh_that_clears_the_divergence_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A phantom catch-up must not be reported once the pointer is fresh.

    The stale local read said the target was ahead; the forced refresh
    proves it is not. Recording a suppressed cross-agent catch-up there
    would be reporting a divergence that does not exist.
    """
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    ancestry = iter([False, False, True, True])

    monkeypatch.setattr(
        auto_integrate,
        "is_ancestor",
        lambda root, ancestor, descendant: next(ancestry),
    )
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(
        config, scope, RebaseState()
    )

    assert len(calls) == 2
    assert second is None, "a refreshed pointer showing no divergence is quiet"


def test_the_forced_refresh_arms_the_throttle_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A burst of dirty boundaries must not turn into a fetch storm."""
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    outcomes = [
        auto_integrate.auto_integrate_on_phase_transition(
            config, scope, RebaseState()
        )
        for _ in range(4)
    ]

    assert len(calls) == 4, (
        "every divergent suppressed boundary must re-read the target before "
        "recording its verdict"
    )
    assert all(outcome is not None for outcome in outcomes)
    assert all(
        outcome.last_refresh == REFRESH_REFRESHED
        for outcome in outcomes
        if outcome is not None
    )


# ---------------------------------------------------------------------------
# Crash recovery reads the same pointer, and its verdict is destructive
# ---------------------------------------------------------------------------
#
# These live here rather than in tests/test_auto_integrate_recovery.py
# because that file is marked ``subprocess_e2e`` at module level and is
# NOT in the ``test-auto-integrate-e2e`` list, so a deterministic test
# added there would be executed by no ``make verify`` step at all. The
# subject is the same one this file already owns: an auto-integration
# verdict must be taken from a pointer that was re-read.


def _integrated_record() -> IntegrationRecord:
    """A durable record whose rebase finished but whose landing did not."""
    return IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
        integrated_feature_sha=_FEATURE_SHA,
    )


def _install_recovery_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_sha: str,
    ancestor: bool,
    on_refresh: Callable[[], str] | None = None,
) -> list[str]:
    """Fake every git and record call recovery makes; record refreshes.

    ``branch_sha`` and ``is_ancestor`` are read AFTER the refresh seam,
    so the returned list ordering is what proves the refresh happened
    first.
    """
    events: list[str] = []
    monkeypatch.setattr(recovery, "_read_record", lambda _root: _integrated_record())
    monkeypatch.setattr(recovery, "_clear_record", lambda _root: events.append("clear"))
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: False)
    monkeypatch.setattr(recovery, "merge_state", lambda _root: MERGE_STATE_NONE)

    def _branch_sha(_root: Path, _ref: str) -> str:
        events.append("branch_sha")
        return target_sha

    def _is_ancestor(_root: Path, _ancestor: str, _descendant: str) -> bool:
        events.append("is_ancestor")
        return ancestor

    def _refresh(_config: UnifiedConfig, _root: Path, _target: str) -> str:
        events.append("refresh")
        return on_refresh() if on_refresh is not None else REFRESH_REFRESHED

    monkeypatch.setattr(recovery, "branch_sha", _branch_sha)
    monkeypatch.setattr(recovery, "is_ancestor", _is_ancestor)
    monkeypatch.setattr(recovery, "_refresh_target", _refresh)
    monkeypatch.setattr(
        recovery, "fast_forward_target", lambda _root, _target, _sha: (True, "")
    )
    return events


def test_recovery_refreshes_the_pointer_before_its_ancestry_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verdict that CLEARS the durable record must not read a stale ref."""
    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=True
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(tmp_path), config=_config()
    )

    assert events[0] == "refresh", "the pointer must be re-read first"
    assert events.count("refresh") == 1, "exactly one refresh per recovery"
    assert outcome is not None
    assert outcome.last_refresh == REFRESH_REFRESHED


def test_a_refresh_that_reveals_a_landable_target_no_longer_drops_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression: a stale read used to clear the record permanently.

    Before the refresh the local pointer looked diverged; afterwards it
    is an ancestor of the integrated feature SHA, so the landing that
    was about to be discarded proceeds instead.
    """
    ancestry = iter([False, True])
    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=True
    )
    monkeypatch.setattr(
        recovery,
        "is_ancestor",
        lambda _root, _a, _d: next(ancestry, True),
    )

    stale_verdict = recovery._continue_fast_forward_from_record(
        tmp_path, _integrated_record(), None
    )
    fresh_verdict = recovery._continue_fast_forward_from_record(
        tmp_path, _integrated_record(), _config()
    )

    assert stale_verdict.last_action == "skipped"
    assert stale_verdict.last_reason == "recovery: target advanced concurrently"
    assert fresh_verdict.last_action == "recovered"
    assert fresh_verdict.fast_forwarded is True
    assert "refresh" in events


def test_recovery_without_a_config_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The parameter is optional, and omitting it changes nothing."""
    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=True
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert "refresh" not in events
    assert outcome is not None
    assert outcome.last_action == "recovered"
    assert outcome.last_refresh is None


def test_a_raising_refresh_never_escapes_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recovery's never-raises contract covers the new seam too.

    The refresh raising is swallowed -- and, because nothing then
    vouches for the target pointer, the landing is DEFERRED rather
    than decided from it.
    """

    def _explode() -> str:
        raise RuntimeError("origin unreachable")

    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=True, on_refresh=_explode
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(tmp_path), config=_config()
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "clear" not in events, "a failed refresh must not drop the record"


def test_a_raising_refresh_cannot_clear_the_record_from_a_stale_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The destructive combination: refresh raises AND the local ref diverged.

    ``_refresh_before_verdict`` used to swallow the raise into ``None``
    and let the caller carry on, so the UNREFRESHED local pointer still
    reached the "target advanced concurrently" branch and CLEARED the
    durable record permanently -- exactly the stale-pointer discard the
    refresh seam exists to prevent. The verdict must now fail closed:
    no ancestry decision, no clear, record kept for the next startup.
    """

    def _explode() -> str:
        raise RuntimeError("origin unreachable")

    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=False, on_refresh=_explode
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(tmp_path), config=_config()
    )

    assert outcome is not None
    assert "clear" not in events, "the durable record must be retained for retry"
    assert "is_ancestor" not in events, (
        "no ancestry verdict may be taken from an unrefreshed pointer"
    )
    assert "branch_sha" not in events
    assert outcome.last_action == "skipped"
    assert outcome.last_reason is not None
    assert "record retained for retry" in outcome.last_reason
    assert outcome.last_refresh == REFRESH_UNREACHABLE, (
        "the skip must name why the pointer could not be trusted"
    )
    assert outcome.fast_forwarded is False


def test_an_unhealthy_refresh_outcome_also_defers_the_recovery_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refresh that RETURNS unreachable is no fresher than one that raised.

    ``refresh_outcome_is_healthy`` is the repository's single predicate
    for "this pointer was confirmed current", so recovery fails closed
    on every unhealthy verb, not just on the exceptional path.
    """
    events = _install_recovery_seams(
        monkeypatch,
        target_sha=_TARGET_SHA,
        ancestor=False,
        on_refresh=lambda: REFRESH_UNREACHABLE,
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(tmp_path), config=_config()
    )

    assert outcome is not None
    assert "clear" not in events
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_UNREACHABLE


def test_a_healthy_refresh_still_reaches_the_ancestry_verdicts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail-closed must not swallow the ordinary diverged-target verdict.

    With a CONFIRMED-current pointer, "target advanced concurrently" is
    still a permanent state and still clears the record.
    """
    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=False
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(tmp_path), config=_config()
    )

    assert outcome is not None
    assert events[0] == "refresh"
    assert "clear" in events
    assert outcome.last_reason == "recovery: target advanced concurrently"
    assert outcome.last_refresh == REFRESH_REFRESHED
