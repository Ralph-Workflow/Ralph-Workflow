from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import auto_integrate
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline import auto_integrate_recovery as recovery
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_REFRESHED,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.integration_resolution import TreeReadinessAssessment
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable


_TARGET_SHA = "1111111111111111111111111111111111111111"
_HEAD_SHA = "2222222222222222222222222222222222222222"
_FEATURE_SHA = "3333333333333333333333333333333333333333"
_ROOT_A = "/workspace/agent-a"
_ROOT_B = "/workspace/agent-b"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _permit_and_succeed(throttle: BoundaryRefreshThrottle, root: str, target: str) -> bool:
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
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)

    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_A, "main") is False
    assert throttle.should_refresh(_ROOT_B, "main") is True


def test_each_target_branch_gets_its_own_window() -> None:
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock)

    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_A, "main") is False
    assert throttle.should_refresh(_ROOT_A, "develop") is True


def test_an_unsuccessful_refresh_does_not_consume_the_window() -> None:
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
    clock = _FakeClock()
    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock, max_tracked_keys=2)

    assert _permit_and_succeed(throttle, _ROOT_A, "main") is True
    assert _permit_and_succeed(throttle, _ROOT_B, "main") is True
    assert _permit_and_succeed(throttle, "/workspace/agent-c", "main") is True

    assert throttle.should_refresh(_ROOT_A, "main") is True
    assert throttle.should_refresh(_ROOT_B, "main") is False


def _dirty_boundary_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> WorkspaceScope:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        boundary,
        "assess_tree_readiness",
        lambda root: TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=(
                "worktree not clean; uncommitted tracked changes deferred catch-up integration",
            ),
        ),
    )
    monkeypatch.setattr(auto_integrate, "resolve_integration_target", lambda config, root: "main")
    monkeypatch.setattr(boundary, "branch_sha", lambda root, ref: _TARGET_SHA)
    monkeypatch.setattr(boundary, "get_head_sha", lambda root: _HEAD_SHA)
    monkeypatch.setattr(boundary, "is_ancestor", lambda root, ancestor, descendant: False)
    monkeypatch.setattr(
        boundary,
        "BOUNDARY_REFRESH_THROTTLE",
        BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=_FakeClock()),
    )
    return WorkspaceScope(tmp_path)


@lru_cache(maxsize=1)
def _config() -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": True,
            "auto_integrate_target": "main",
        }
    }
    return UnifiedConfig.model_validate(payload)


def test_dirty_boundary_records_the_refresh_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    refresh_calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_REFRESHED

    monkeypatch.setattr(boundary, "_refresh_target", _fake_refresh)

    outcome = auto_integrate.auto_integrate_on_phase_transition(_config(), scope, RebaseState())

    assert refresh_calls == ["main"], "the boundary must observe a fresh pointer"
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_REFRESHED


def test_throttled_boundary_records_the_suppression_without_refetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    ancestry = iter([False, True, True])
    monkeypatch.setattr(
        boundary,
        "is_ancestor",
        lambda root, ancestor, descendant: next(ancestry),
    )
    refresh_calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_REFRESHED

    monkeypatch.setattr(boundary, "_refresh_target", _fake_refresh)
    config = _config()

    first = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert len(refresh_calls) == 1, "the throttle must suppress the second fetch"
    assert first is not None
    assert first.last_refresh == REFRESH_REFRESHED
    assert second is None, "a contained target is the quiet, fetch-free path"


def test_a_failed_boundary_refresh_is_retried_on_the_next_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    refresh_calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_UNREACHABLE

    monkeypatch.setattr(boundary, "_refresh_target", _fake_refresh)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert refresh_calls == ["main", "main"], (
        "a refresh that could not reach origin must not consume the window"
    )
    assert second is not None
    assert second.last_refresh == REFRESH_UNREACHABLE


def _recording_refresh(monkeypatch: pytest.MonkeyPatch, outcome: str) -> list[str]:
    calls: list[str] = []

    def _fake_refresh(config: UnifiedConfig, root: Path, target: str) -> str:
        calls.append(target)
        return outcome

    monkeypatch.setattr(boundary, "_refresh_target", _fake_refresh)
    return calls


def test_a_throttled_boundary_with_a_pending_catch_up_forces_one_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert len(calls) == 2, "the suppressed boundary must force one refresh"
    assert second is not None
    assert second.last_action == "skipped"
    assert second.last_refresh == REFRESH_REFRESHED, (
        "the recorded verdict must carry the real refresh outcome, not REFRESH_SUPPRESSED"
    )


def test_a_throttled_boundary_with_nothing_pending_still_costs_no_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(boundary, "is_ancestor", lambda root, ancestor, descendant: True)
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    first = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert first is None
    assert second is None
    assert len(calls) == 1, "only the throttled first boundary may fetch"


def test_a_forced_refresh_that_clears_the_divergence_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    ancestry = iter([False, False, True, True])

    monkeypatch.setattr(
        boundary,
        "is_ancestor",
        lambda root, ancestor, descendant: next(ancestry),
    )
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    second = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert len(calls) == 2
    assert second is None, "a refreshed pointer showing no divergence is quiet"


@pytest.mark.timeout_seconds(5)
def test_the_forced_refresh_arms_the_throttle_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    clock = _FakeClock()
    monkeypatch.setattr(
        boundary,
        "BOUNDARY_REFRESH_THROTTLE",
        BoundaryRefreshThrottle(min_interval_seconds=30.0, clock=clock),
    )
    calls = _recording_refresh(monkeypatch, REFRESH_REFRESHED)
    config = _config()

    first = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    forced = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    suppressed = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert calls == ["main", "main"]
    assert first is not None
    assert forced is not None
    assert forced.last_refresh == REFRESH_REFRESHED
    assert suppressed is not None
    assert suppressed.last_refresh == REFRESH_SUPPRESSED

    clock.advance(30.0)
    later = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert calls == ["main", "main", "main"]
    assert later is not None
    assert later.last_refresh == REFRESH_REFRESHED


@pytest.mark.timeout_seconds(5)
def test_an_unhealthy_forced_refresh_is_retried_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = _dirty_boundary_workspace(monkeypatch, tmp_path)
    outcomes = iter([REFRESH_REFRESHED, REFRESH_UNREACHABLE, REFRESH_UNREACHABLE])
    calls: list[str] = []

    def _fake_refresh(_config: UnifiedConfig, _root: Path, target: str) -> str:
        calls.append(target)
        return next(outcomes)

    monkeypatch.setattr(boundary, "_refresh_target", _fake_refresh)
    config = _config()

    auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    first_forced = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())
    retry = auto_integrate.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert calls == ["main", "main", "main"]
    assert first_forced is not None
    assert first_forced.last_refresh == REFRESH_UNREACHABLE
    assert retry is not None
    assert retry.last_refresh == REFRESH_UNREACHABLE


def _integrated_record() -> IntegrationRecord:
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
    events: list[str] = []
    monkeypatch.setattr(recovery, "_read_record", lambda _root, **_kwargs: _integrated_record())
    landing = importlib.import_module("ralph.pipeline._recovery_fast_forward_landing")
    monkeypatch.setattr(recovery, "_clear_record", lambda _root: events.append("clear"))
    monkeypatch.setattr(landing, "_clear_record", lambda _root: events.append("clear"))
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: False)
    monkeypatch.setattr(recovery, "merge_state", lambda _root: MERGE_STATE_NONE)
    monkeypatch.setattr(recovery, "_head_matches_sha", lambda _root, _sha: True)

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
    monkeypatch.setattr(recovery, "ancestry_state", _is_ancestor)
    monkeypatch.setattr(recovery, "_refresh_target", _refresh)
    monkeypatch.setattr(
        landing,
        "fast_forward_target",
        lambda _root, _target, _sha, **_kwargs: events.append("fast_forward") or (True, ""),
    )
    monkeypatch.setattr(landing, "post_attempt_verify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(landing, "_delete_rebase_backup_refs", lambda _root, _ref: None)
    monkeypatch.setattr(
        landing,
        "reconcile_remote_publication",
        lambda _config, _root, _target, **_kw: _kw.get("source_state"),
    )
    return events


@pytest.mark.timeout_seconds(5)
def test_recovery_refreshes_the_pointer_before_its_ancestry_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events = _install_recovery_seams(monkeypatch, target_sha=_TARGET_SHA, ancestor=True)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path), config=_config())

    assert events[0] == "refresh", "the pointer must be re-read first"
    assert events.count("refresh") == 1, "exactly one refresh per recovery"
    assert outcome is not None
    assert outcome.last_refresh == REFRESH_REFRESHED


def test_a_refresh_that_reveals_a_landable_target_no_longer_drops_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events = _install_recovery_seams(monkeypatch, target_sha=_TARGET_SHA, ancestor=True)

    fresh_verdict = recovery._continue_fast_forward_from_record(
        tmp_path, _integrated_record(), _config()
    )

    assert fresh_verdict.last_action == "recovered"
    assert fresh_verdict.fast_forwarded is True
    assert events == ["refresh", "branch_sha", "is_ancestor", "fast_forward", "clear"]


def test_recovery_without_a_config_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events = _install_recovery_seams(monkeypatch, target_sha=_TARGET_SHA, ancestor=True)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert "refresh" not in events
    assert outcome is not None
    assert outcome.last_action == "recovered"
    assert outcome.last_refresh is None


def test_a_raising_refresh_never_escapes_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def _explode() -> str:
        raise RuntimeError("origin unreachable")

    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=True, on_refresh=_explode
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path), config=_config())

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "clear" not in events, "a failed refresh must not drop the record"


def test_a_raising_refresh_cannot_clear_the_record_from_a_stale_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def _explode() -> str:
        raise RuntimeError("origin unreachable")

    events = _install_recovery_seams(
        monkeypatch, target_sha=_TARGET_SHA, ancestor=False, on_refresh=_explode
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path), config=_config())

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
    events = _install_recovery_seams(
        monkeypatch,
        target_sha=_TARGET_SHA,
        ancestor=False,
        on_refresh=lambda: REFRESH_UNREACHABLE,
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path), config=_config())

    assert outcome is not None
    assert "clear" not in events
    assert outcome.last_action == "skipped"
    assert outcome.last_refresh == REFRESH_UNREACHABLE
