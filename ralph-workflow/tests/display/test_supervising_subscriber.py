from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState
from ralph.supervising import InstanceStatus, WorkflowInstanceView, instance_view_from_snapshot

_EXPECTED_NOTIFICATION_COUNT = 3

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot


def _make_subscriber(
    tmp_path: Path,
    run_id: str = "run-test",
    on_snapshot=None,
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


def test_on_snapshot_receives_active_view_on_notify(tmp_path: Path) -> None:
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


def test_on_snapshot_view_shows_waiting_status(tmp_path: Path) -> None:
    received: list[WorkflowInstanceView] = []
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-wait",
        on_snapshot=lambda snap: received.append(instance_view_from_snapshot(snap)),
    )
    subscriber.notify(_make_state("development"))
    _drain_views(received)

    subscriber.record_waiting_status(
        WaitingStatusEvent(
            kind=WaitingStatusKind.SUSPECTED_FROZEN,
            cumulative_seconds=120.0,
            current_run_seconds=60.0,
            idle_elapsed_seconds=15.0,
            ceiling_seconds=1800.0,
            suspect_threshold_seconds=600.0,
            diagnostic={"evidence": "time_only"},
        )
    )

    assert received
    view = received[-1]
    assert view.lifecycle_status == InstanceStatus.WAITING
    assert view.instance_id == "run-wait"
    assert view.current_stage == "development"


def test_on_snapshot_instance_id_stable_across_notifications(tmp_path: Path) -> None:
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


def test_on_snapshot_view_contains_recent_activity_after_record_activity(tmp_path: Path) -> None:
    received: list[WorkflowInstanceView] = []
    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-activity",
        on_snapshot=lambda snap: received.append(instance_view_from_snapshot(snap)),
    )
    subscriber.notify(_make_state("development"))
    _drain_views(received)

    subscriber.record_activity(unit_id="u1", line="running mypy checks")

    assert received
    assert any("running mypy checks" in activity for activity in received[-1].recent_activity)


def test_on_snapshot_exception_does_not_propagate_to_notify(tmp_path: Path) -> None:
    def _raise(snap: object) -> None:
        raise ValueError("callback error")

    subscriber = _make_subscriber(
        tmp_path,
        run_id="run-exc",
        on_snapshot=_raise,
    )

    subscriber.notify(_make_state("development"))

    assert not subscriber.queue.empty()
