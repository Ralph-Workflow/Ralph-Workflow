from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState
from ralph.supervising import (
    InstanceStatus,
    WorkflowInstanceTracker,
    WorkflowInstanceView,
    instance_view_from_snapshot,
)

_EXPECTED_NOTIFICATION_COUNT = 3

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot


def _make_subscriber(
    tmp_path: Path,
    run_id: str = "run-test",
    on_snapshot: Callable[[PipelineSnapshot], None] | None = None,
) -> PipelineSubscriber:
    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=64)
    return PipelineSubscriber(
        queue=q,
        workspace_root=tmp_path,
        run_id=run_id,
        on_snapshot=on_snapshot,
    )


def _make_state(phase: str = "development") -> PipelineState:
    return PipelineState(phase=phase, budget_caps={"iteration": 1, "reviewer_pass": 1})


def _drain_views(received: list[WorkflowInstanceView]) -> None:
    received.clear()


def test_tracker_on_snapshot_receives_active_view_on_notify(tmp_path: Path) -> None:
    """PipelineSubscriber with tracker.update_from_snapshot publishes active view."""
    tracker = WorkflowInstanceTracker(instance_id="work-xyz")
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-xyz",
        on_snapshot=tracker.update_from_snapshot,
    )

    subscriber.notify(_make_state("development"))

    assert tracker.view.instance_id == "work-xyz"
    assert tracker.view.run_id == "run-xyz"
    assert tracker.view.lifecycle_status == InstanceStatus.ACTIVE
    assert tracker.view.current_stage == "development"


def test_tracker_on_snapshot_view_shows_waiting_status(tmp_path: Path) -> None:
    """Tracker view reflects WAITING status when waiting status is recorded."""
    tracker = WorkflowInstanceTracker(instance_id="work-wait")
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-wait",
        on_snapshot=tracker.update_from_snapshot,
    )
    subscriber.notify(_make_state("development"))
    _drain_views([])

    subscriber.record_waiting_status(
        WaitingStatusEvent(
            kind=WaitingStatusKind.SUSPECTED_FROZEN,
            cumulative_seconds=120.0,
            current_run_seconds=60.0,
            idle_elapsed_seconds=15.0,
            ceiling_seconds=1800.0,
            suspect_threshold_seconds=600.0,
            diagnostic={"evidence": "time_only"},
            subagent_activity="scout exploring",
        )
    )

    assert tracker.view.lifecycle_status == InstanceStatus.WAITING
    assert tracker.view.instance_id == "work-wait"
    assert tracker.view.run_id == "run-wait"
    assert tracker.view.current_stage == "development"
    # The tracker view must surface the subagent_activity in its
    # recent_activity (which exposes snapshot.waiting_status_line) so
    # supervising tooling can show what the subagent was doing at the
    # moment of the SUSPECTED_FROZEN event.
    assert any(
        "subagent=scout exploring" in line
        for line in tracker.view.recent_activity
    )


def test_tracker_on_snapshot_instance_id_stable_across_notifications(tmp_path: Path) -> None:
    """Stable instance_id is preserved across all notifications."""
    tracker = WorkflowInstanceTracker(instance_id="work-stable")
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-stable",
        on_snapshot=tracker.update_from_snapshot,
    )

    for phase in ("development", "planning", "development"):
        subscriber.notify(_make_state(phase))

    # instance_id and run_id remain stable across all notifications
    assert tracker.view.instance_id == "work-stable"
    assert tracker.view.run_id == "run-stable"
    assert tracker.view.lifecycle_status == InstanceStatus.ACTIVE


def test_tracker_on_snapshot_view_contains_recent_activity_after_record_activity(
    tmp_path: Path,
) -> None:
    """Tracker view reflects recent activity after record_activity call."""
    tracker = WorkflowInstanceTracker(instance_id="work-act")
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-activity",
        on_snapshot=tracker.update_from_snapshot,
    )
    subscriber.notify(_make_state("development"))
    _drain_views([])

    subscriber.record_activity(unit_id="u1", line="running mypy checks")

    assert tracker.view.instance_id == "work-act"
    assert tracker.view.run_id == "run-activity"
    assert any("running mypy checks" in activity for activity in tracker.view.recent_activity)


def test_tracker_view_keeps_activity_visible_during_waiting_state(tmp_path: Path) -> None:
    """Waiting status must not hide the last meaningful activity in tracker view."""
    tracker = WorkflowInstanceTracker(instance_id="work-wait-activity")
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-wait-activity",
        on_snapshot=tracker.update_from_snapshot,
    )
    subscriber.notify(_make_state("development"))
    subscriber.record_activity(
        unit_id="u1",
        line="tool output that broke",
        agent_name="opencode/minimax/MiniMax-M3",
    )
    subscriber.record_waiting_status(
        WaitingStatusEvent(
            kind=WaitingStatusKind.PROGRESS,
            cumulative_seconds=120.0,
            current_run_seconds=60.0,
            idle_elapsed_seconds=15.0,
            ceiling_seconds=1800.0,
            suspect_threshold_seconds=600.0,
            diagnostic={},
        ),
        agent_name="opencode/minimax/MiniMax-M3",
    )

    assert tracker.view.lifecycle_status == InstanceStatus.WAITING
    assert tracker.view.recent_activity[-2:] == (
        "tool output that broke",
        "Background child work still active "
        "(run=60s, cumulative=120s, ceiling=1800s, "
        "agent=opencode/minimax/MiniMax-M3)",
    )


def test_tracker_on_snapshot_exception_does_not_propagate_to_notify(tmp_path: Path) -> None:
    """Callback exceptions are isolated and do not propagate to notify()."""

    def _raise(snap: object) -> None:
        raise ValueError("callback error")

    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-exc",
        on_snapshot=_raise,
    )

    subscriber.notify(_make_state("development"))

    assert not subscriber.queue.empty()


def test_tracker_view_accessible_before_any_notification(tmp_path: Path) -> None:
    """Tracker.view is accessible and shows NOT_STARTED before any snapshot."""
    tracker = WorkflowInstanceTracker(instance_id="work-pre")
    assert tracker.view.instance_id == "work-pre"
    assert tracker.view.run_id is None
    assert tracker.view.lifecycle_status == InstanceStatus.NOT_STARTED
    assert tracker.view.current_stage is None
    assert tracker.view.recent_activity == ()


def test_tracker_run_id_reflects_runtime_identity(tmp_path: Path) -> None:
    """The tracker's view.run_id is separate from instance_id and tracks runtime identity."""
    tracker = WorkflowInstanceTracker(instance_id="work-separate")
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-runtime",
        on_snapshot=tracker.update_from_snapshot,
    )

    subscriber.notify(_make_state("development"))

    assert tracker.view.instance_id == "work-separate"
    assert tracker.view.run_id == "run-runtime"


def test_on_snapshot_receives_active_view_on_notify_lambda(
    tmp_path: Path,
) -> None:
    """Direct lambda using instance_view_from_snapshot also works (backwards compat)."""
    received: list[WorkflowInstanceView] = []
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-xyz",
        on_snapshot=lambda snap: received.append(instance_view_from_snapshot(snap)),
    )

    subscriber.notify(_make_state("development"))

    assert received
    view = received[-1]
    assert view.instance_id == "run-xyz"
    assert view.lifecycle_status == InstanceStatus.ACTIVE
    assert view.current_stage == "development"


def test_on_snapshot_instance_id_stable_across_notifications_lambda(
    tmp_path: Path,
) -> None:
    """Lambda-based callback preserves run_id across notifications (backwards compat)."""
    received: list[WorkflowInstanceView] = []
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-stable",
        on_snapshot=lambda snap: received.append(instance_view_from_snapshot(snap)),
    )

    for phase in ("development", "planning", "development"):
        subscriber.notify(_make_state(phase))

    assert len(received) >= _EXPECTED_NOTIFICATION_COUNT
    assert all(view.instance_id == "run-stable" for view in received)
