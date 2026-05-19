from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from ralph.display.snapshot import PipelineSnapshot
from ralph.supervising import (
    InstanceStatus,
    WorkflowInstanceTracker,
    WorkflowInstanceView,
    instance_view_from_snapshot,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _snap(**kwargs: object) -> PipelineSnapshot:
    defaults: dict[str, Any] = {
        "phase": "development",
        "previous_phase": None,
        "review_issues_found": False,
        "interrupted_by_user": False,
        "last_error": None,
        "pr_url": None,
        "push_count": 0,
        "total_agent_calls": 0,
        "total_continuations": 0,
        "total_fallbacks": 0,
        "total_retries": 0,
        "workers": (),
        "prompt_path": None,
        "prompt_preview": (),
        "run_id": "run-abc",
        "created_at": _NOW,
    }
    defaults.update(kwargs)
    return PipelineSnapshot(**defaults)


def test_instance_status_values() -> None:
    assert InstanceStatus.NOT_STARTED == "not_started"
    assert InstanceStatus.ACTIVE == "active"
    assert InstanceStatus.WAITING == "waiting"
    assert InstanceStatus.COMPLETED == "completed"
    assert InstanceStatus.FAILED == "failed"


def test_workflow_instance_view_is_frozen_and_slotted() -> None:
    view = WorkflowInstanceView(
        instance_id="run-1",
        run_id="run-abc",
        lifecycle_status=InstanceStatus.ACTIVE,
        current_stage="development",
        recent_activity=("line 1",),
    )

    assert view.instance_id == "run-1"
    assert view.run_id == "run-abc"
    assert view.recent_activity == ("line 1",)
    assert not hasattr(view, "__dict__")
    with pytest.raises(FrozenInstanceError):
        view.__setattr__("instance_id", "run-2")


# =============================================================================
# WorkflowInstanceTracker tests
# =============================================================================


def test_tracker_starts_at_not_started() -> None:
    """Tracker initialized with a stable instance_id starts at NOT_STARTED."""
    tracker = WorkflowInstanceTracker(instance_id="work-42")
    view = tracker.view
    assert view.lifecycle_status == InstanceStatus.NOT_STARTED
    assert view.instance_id == "work-42"
    assert view.run_id is None
    assert view.current_stage is None
    assert view.recent_activity == ()


def test_tracker_instance_id_is_stable() -> None:
    """The tracker instance_id cannot be changed after construction."""
    tracker = WorkflowInstanceTracker(instance_id="work-42")
    with pytest.raises(FrozenInstanceError):
        tracker._view.__setattr__("instance_id", "changed")


def test_tracker_requires_non_empty_instance_id() -> None:
    """Tracker construction rejects empty instance_id."""
    with pytest.raises(ValueError, match="non-empty"):
        WorkflowInstanceTracker(instance_id="")


def test_two_trackers_are_distinguishable_before_startup() -> None:
    """Two trackers created before startup have distinct stable identities."""
    tracker1 = WorkflowInstanceTracker(instance_id="work-1")
    tracker2 = WorkflowInstanceTracker(instance_id="work-2")
    assert tracker1.view.instance_id == "work-1"
    assert tracker2.view.instance_id == "work-2"
    assert tracker1.view.instance_id != tracker2.view.instance_id


def test_tracker_update_from_snapshot_preserves_stable_instance_id() -> None:
    """update_from_snapshot preserves the stable instance_id from construction."""
    tracker = WorkflowInstanceTracker(instance_id="work-stable")
    tracker.update_from_snapshot(_snap(run_id="run-abc"))
    assert tracker.view.instance_id == "work-stable"
    assert tracker.view.run_id == "run-abc"


def test_tracker_update_from_snapshot_fills_run_id() -> None:
    """The view's run_id is copied from the snapshot when present."""
    tracker = WorkflowInstanceTracker(instance_id="work-42")
    tracker.update_from_snapshot(_snap(run_id="run-abc"))
    assert tracker.view.run_id == "run-abc"


def test_tracker_update_from_snapshot_with_no_run_id() -> None:
    """A snapshot without a run_id clears the view's run_id."""
    tracker = WorkflowInstanceTracker(instance_id="work-42")
    tracker.update_from_snapshot(_snap(run_id=None))
    assert tracker.view.instance_id == "work-42"
    assert tracker.view.run_id is None


def test_tracker_waiting_preserves_stage() -> None:
    """Waiting state retains the current stage."""
    tracker = WorkflowInstanceTracker(instance_id="work-wait")
    tracker.update_from_snapshot(_snap(waiting_status_line="waiting on child"))
    assert tracker.view.lifecycle_status == InstanceStatus.WAITING
    assert tracker.view.current_stage == "development"


def test_tracker_completed_clears_stage() -> None:
    """Completed state clears the current stage."""
    tracker = WorkflowInstanceTracker(instance_id="work-done")
    tracker.update_from_snapshot(_snap(is_terminal_success=True))
    assert tracker.view.lifecycle_status == InstanceStatus.COMPLETED
    assert tracker.view.current_stage is None


def test_tracker_failed_on_terminal_failure_clears_stage() -> None:
    """Failed state (terminal failure) clears the current stage."""
    tracker = WorkflowInstanceTracker(instance_id="work-fail")
    tracker.update_from_snapshot(_snap(is_terminal_failure=True))
    assert tracker.view.lifecycle_status == InstanceStatus.FAILED
    assert tracker.view.current_stage is None


def test_tracker_failed_on_interrupt_clears_stage() -> None:
    """Failed state (user interrupt) clears the current stage."""
    tracker = WorkflowInstanceTracker(instance_id="work-int")
    tracker.update_from_snapshot(_snap(interrupted_by_user=True))
    assert tracker.view.lifecycle_status == InstanceStatus.FAILED
    assert tracker.view.current_stage is None


def test_tracker_active_with_unset_phase_yields_none_stage() -> None:
    """Active phase=__unset__ yields current_stage=None (not an unknown state)."""
    tracker = WorkflowInstanceTracker(instance_id="work-unset")
    tracker.update_from_snapshot(_snap(phase="__unset__"))
    assert tracker.view.lifecycle_status == InstanceStatus.ACTIVE
    assert tracker.view.current_stage is None


def test_tracker_update_returns_updated_view() -> None:
    """update_from_snapshot returns the updated view."""
    tracker = WorkflowInstanceTracker(instance_id="work-42")
    snap = _snap(run_id="run-abc")
    result = tracker.update_from_snapshot(snap)
    assert result is tracker.view
    assert result.instance_id == "work-42"
    assert result.run_id == "run-abc"


# =============================================================================
# instance_view_from_snapshot tests (direct projection)
# =============================================================================


def test_active_when_running_without_waiting_status() -> None:
    """Active snapshot without waiting status projects instance_id from run_id."""
    view = instance_view_from_snapshot(_snap())
    assert view.lifecycle_status == InstanceStatus.ACTIVE
    assert view.instance_id == "run-abc"
    assert view.run_id == "run-abc"
    assert view.current_stage == "development"


def test_waiting_when_waiting_status_line_present() -> None:
    """Snapshot with waiting status line projects WAITING status."""
    view = instance_view_from_snapshot(_snap(waiting_status_line="waiting on child"))
    assert view.lifecycle_status == InstanceStatus.WAITING
    assert view.current_stage == "development"


def test_completed_clears_stage() -> None:
    """Terminal success snapshot clears the current stage."""
    view = instance_view_from_snapshot(_snap(is_terminal_success=True))
    assert view.lifecycle_status == InstanceStatus.COMPLETED
    assert view.current_stage is None


def test_failed_clears_stage_on_terminal_failure() -> None:
    """Terminal failure snapshot clears the current stage."""
    view = instance_view_from_snapshot(_snap(is_terminal_failure=True))
    assert view.lifecycle_status == InstanceStatus.FAILED
    assert view.current_stage is None


def test_failed_clears_stage_on_interrupt() -> None:
    """User interrupt snapshot clears the current stage."""
    view = instance_view_from_snapshot(_snap(interrupted_by_user=True))
    assert view.lifecycle_status == InstanceStatus.FAILED
    assert view.current_stage is None


def test_recent_activity_keeps_last_five_decisions() -> None:
    """Recent activity includes the last five decision log entries."""
    log = tuple((f"p{i}", f"d{i}", f"r{i}", f"t{i}") for i in range(6))
    view = instance_view_from_snapshot(_snap(decision_log=log))
    assert view.recent_activity == (
        "t1 | p1 | d1 | r1",
        "t2 | p2 | d2 | r2",
        "t3 | p3 | d3 | r3",
        "t4 | p4 | d4 | r4",
        "t5 | p5 | d5 | r5",
    )


def test_recent_activity_prefers_waiting_status_line() -> None:
    """Recent activity uses waiting_status_line when present."""
    view = instance_view_from_snapshot(
        _snap(
            decision_log=(("p0", "d0", "r0", "t0"),),
            last_activity_line="last activity",
            waiting_status_line="waiting now",
        )
    )
    assert view.recent_activity[-1] == "waiting now"


def test_recent_activity_falls_back_to_last_activity_line() -> None:
    """Recent activity falls back to last_activity_line when no waiting status."""
    view = instance_view_from_snapshot(_snap(last_activity_line="last activity"))
    assert view.recent_activity[-1] == "last activity"


def test_recent_activity_is_a_tuple() -> None:
    """Recent activity is always a tuple (immutable)."""
    view = instance_view_from_snapshot(_snap())
    assert isinstance(view.recent_activity, tuple)


def test_current_stage_is_none_when_phase_is_unset_sentinel() -> None:
    """Active phase with __unset__ sentinel yields current_stage=None."""
    view = instance_view_from_snapshot(_snap(phase="__unset__"))
    assert view.lifecycle_status == InstanceStatus.ACTIVE
    assert view.current_stage is None


def test_two_instances_with_different_run_ids_have_distinct_identities() -> None:
    """Direct projection: different run_ids produce different instance_ids."""
    view1 = instance_view_from_snapshot(_snap(run_id="run-001"))
    view2 = instance_view_from_snapshot(_snap(run_id="run-002"))
    assert view1.instance_id == "run-001"
    assert view2.instance_id == "run-002"
    assert view1.instance_id != view2.instance_id


def test_recent_activity_omits_reason_separator_when_reason_is_empty() -> None:
    """Decision log entries without a reason omit the trailing separator."""
    log = (("planning", "proceed", "", "t0"),)
    view = instance_view_from_snapshot(_snap(decision_log=log))
    assert view.recent_activity == ("t0 | planning | proceed",)


def test_instance_view_from_snapshot_with_identity_override() -> None:
    """Snapshot can be projected with a stable identity override."""
    view = instance_view_from_snapshot(
        _snap(run_id="run-abc"),
        _instance_id_override="work-stable",
    )
    assert view.instance_id == "work-stable"
    assert view.run_id == "run-abc"
    assert view.lifecycle_status == InstanceStatus.ACTIVE


def test_instance_view_from_snapshot_without_run_id_raises() -> None:
    """Direct projection without run_id and without override raises ValueError."""
    snap = _snap(run_id=None)
    with pytest.raises(ValueError, match=r"requires either a non-None snapshot\.run_id"):
        instance_view_from_snapshot(snap)


def test_instance_view_from_snapshot_without_run_id_with_override_succeeds() -> None:
    """Direct projection with override succeeds even when run_id is None."""
    view = instance_view_from_snapshot(
        _snap(run_id=None),
        _instance_id_override="work-stable",
    )
    assert view.instance_id == "work-stable"
    assert view.run_id is None
    assert view.lifecycle_status == InstanceStatus.NOT_STARTED
