"""Trackable workflow instance model for orchestration use cases.

Exposes the minimum product-facing information an external orchestrator needs
to monitor a running Ralph Workflow instance: stable identity, lifecycle status,
current pipeline stage, and recent operational activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.instance_status import InstanceStatus
else:
    InstanceStatus = import_module("ralph.instance_status").InstanceStatus

_UNSET_PHASE = "__unset__"


@dataclass(frozen=True, slots=True)
class WorkflowInstanceView:
    """Immutable view of a single Ralph Workflow instance for orchestration."""

    instance_id: str | None
    lifecycle_status: InstanceStatus
    current_stage: str | None
    recent_activity: tuple[str, ...]


def instance_view_from_snapshot(snapshot: PipelineSnapshot) -> WorkflowInstanceView:
    """Project a PipelineSnapshot into a WorkflowInstanceView."""
    lifecycle_status = _lifecycle_status(snapshot)
    return WorkflowInstanceView(
        instance_id=snapshot.run_id,
        lifecycle_status=lifecycle_status,
        current_stage=_current_stage(snapshot, lifecycle_status),
        recent_activity=_recent_activity(snapshot),
    )


def _lifecycle_status(snapshot: PipelineSnapshot) -> InstanceStatus:
    if snapshot.is_terminal_success:
        return InstanceStatus.COMPLETED
    if snapshot.is_terminal_failure or snapshot.interrupted_by_user:
        return InstanceStatus.FAILED
    if snapshot.run_id is None:
        return InstanceStatus.NOT_STARTED
    if snapshot.waiting_status_line is not None:
        return InstanceStatus.WAITING
    return InstanceStatus.ACTIVE


def _current_stage(snapshot: PipelineSnapshot, status: InstanceStatus) -> str | None:
    if status in (InstanceStatus.COMPLETED, InstanceStatus.FAILED, InstanceStatus.NOT_STARTED):
        return None
    phase = snapshot.phase
    if not phase or phase == _UNSET_PHASE:
        return None
    return phase


def _recent_activity(snapshot: PipelineSnapshot) -> tuple[str, ...]:
    lines: list[str] = []
    for phase, decision, reason, timestamp in snapshot.decision_log[-5:]:
        parts = [timestamp, phase, decision]
        if reason:
            parts.append(reason)
        lines.append(" | ".join(parts))
    if snapshot.waiting_status_line is not None:
        lines.append(snapshot.waiting_status_line)
    elif snapshot.last_activity_line is not None:
        lines.append(snapshot.last_activity_line)
    return tuple(lines)


__all__ = ["InstanceStatus", "WorkflowInstanceView", "instance_view_from_snapshot"]
