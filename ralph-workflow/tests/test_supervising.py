from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from ralph.display.snapshot import PipelineSnapshot
from ralph.supervising import (
    InstanceStatus,
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
        lifecycle_status=InstanceStatus.ACTIVE,
        current_stage="development",
        recent_activity=("line 1",),
    )

    assert view.instance_id == "run-1"
    assert view.recent_activity == ("line 1",)
    assert not hasattr(view, "__dict__")
    with pytest.raises(FrozenInstanceError):
        view.__setattr__("instance_id", "run-2")


def test_not_started_when_no_run_id() -> None:
    view = instance_view_from_snapshot(_snap(run_id=None))
    assert view.lifecycle_status == InstanceStatus.NOT_STARTED
    assert view.instance_id is None
    assert view.current_stage is None


def test_active_when_running_without_waiting_status() -> None:
    view = instance_view_from_snapshot(_snap())
    assert view.lifecycle_status == InstanceStatus.ACTIVE
    assert view.instance_id == "run-abc"
    assert view.current_stage == "development"


def test_waiting_when_waiting_status_line_present() -> None:
    view = instance_view_from_snapshot(_snap(waiting_status_line="waiting on child"))
    assert view.lifecycle_status == InstanceStatus.WAITING
    assert view.current_stage == "development"


def test_completed_clears_stage() -> None:
    view = instance_view_from_snapshot(_snap(is_terminal_success=True))
    assert view.lifecycle_status == InstanceStatus.COMPLETED
    assert view.current_stage is None


def test_failed_clears_stage_on_terminal_failure() -> None:
    view = instance_view_from_snapshot(_snap(is_terminal_failure=True))
    assert view.lifecycle_status == InstanceStatus.FAILED
    assert view.current_stage is None


def test_failed_clears_stage_on_interrupt() -> None:
    view = instance_view_from_snapshot(_snap(interrupted_by_user=True))
    assert view.lifecycle_status == InstanceStatus.FAILED
    assert view.current_stage is None


def test_recent_activity_keeps_last_five_decisions() -> None:
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
    view = instance_view_from_snapshot(
        _snap(
            decision_log=(("p0", "d0", "r0", "t0"),),
            last_activity_line="last activity",
            waiting_status_line="waiting now",
        )
    )
    assert view.recent_activity[-1] == "waiting now"


def test_recent_activity_falls_back_to_last_activity_line() -> None:
    view = instance_view_from_snapshot(_snap(last_activity_line="last activity"))
    assert view.recent_activity[-1] == "last activity"


def test_recent_activity_is_a_tuple() -> None:
    view = instance_view_from_snapshot(_snap())
    assert isinstance(view.recent_activity, tuple)


def test_current_stage_is_none_when_phase_is_unset_sentinel() -> None:
    view = instance_view_from_snapshot(_snap(phase="__unset__"))
    assert view.lifecycle_status == InstanceStatus.ACTIVE
    assert view.current_stage is None


def test_two_instances_with_different_run_ids_have_distinct_identities() -> None:
    view1 = instance_view_from_snapshot(_snap(run_id="run-001"))
    view2 = instance_view_from_snapshot(_snap(run_id="run-002"))
    assert view1.instance_id == "run-001"
    assert view2.instance_id == "run-002"
    assert view1.instance_id != view2.instance_id


def test_recent_activity_omits_reason_separator_when_reason_is_empty() -> None:
    log = (("planning", "proceed", "", "t0"),)
    view = instance_view_from_snapshot(_snap(decision_log=log))
    assert view.recent_activity == ("t0 | planning | proceed",)
