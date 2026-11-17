from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.instance_status import InstanceStatus

if TYPE_CHECKING:
    from ralph._supervising_tracker import WorkflowInstanceTracker
    from ralph.display.snapshot import PipelineSnapshot

__all__ = [
    "InstanceStatus",
    "WorkflowInstanceTracker",
    "WorkflowInstanceView",
    "instance_view_from_snapshot",
]

_UNSET_PHASE = "__unset__"


@dataclass(frozen=True, slots=True)
class WorkflowInstanceView:
    instance_id: str
    run_id: str | None
    lifecycle_status: InstanceStatus
    current_stage: str | None
    recent_activity: tuple[str, ...]


def instance_view_from_snapshot(
    snapshot: PipelineSnapshot,
    *,
    _instance_id_override: str | None = None,
) -> WorkflowInstanceView:
    lifecycle_status = _lifecycle_status(snapshot)
    if _instance_id_override is not None:
        instance_id = _instance_id_override
    elif snapshot.run_id is not None:
        instance_id = snapshot.run_id
    else:
        raise ValueError(
            "instance_view_from_snapshot requires either a non-None "
            "snapshot.run_id or an explicit _instance_id_override. "
            "For tracker-based supervision, use "
            "WorkflowInstanceTracker.update_from_snapshot instead."
        )
    return WorkflowInstanceView(
        instance_id=instance_id,
        run_id=snapshot.run_id,
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
    if status in (
        InstanceStatus.COMPLETED,
        InstanceStatus.FAILED,
        InstanceStatus.NOT_STARTED,
    ):
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
    if snapshot.last_activity_line is not None:
        lines.append(snapshot.last_activity_line)
    if snapshot.waiting_status_line is not None:
        lines.append(snapshot.waiting_status_line)
    return tuple(lines)


def __getattr__(name: str) -> object:
    if name == "WorkflowInstanceTracker":
        from ralph._supervising_tracker import WorkflowInstanceTracker

        return WorkflowInstanceTracker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
