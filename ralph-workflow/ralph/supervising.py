from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph._supervising_tracker import WorkflowInstanceTracker
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.instance_status import InstanceStatus
else:
    InstanceStatus = import_module("ralph.instance_status").InstanceStatus

__all__ = [
    "InstanceStatus",
    "WorkflowInstanceTracker",
    "WorkflowInstanceView",
    "instance_view_from_snapshot",
]

_UNSET_PHASE = "__unset__"


class WorkflowInstanceView:
    _current_stage: str | None
    _instance_id: str
    _lifecycle_status: InstanceStatus
    _recent_activity: tuple[str, ...]
    _run_id: str | None

    __slots__ = (
        "_current_stage",
        "_instance_id",
        "_lifecycle_status",
        "_recent_activity",
        "_run_id",
    )

    def __init__(
        self,
        instance_id: str,
        run_id: str | None,
        lifecycle_status: InstanceStatus,
        current_stage: str | None,
        recent_activity: tuple[str, ...],
    ) -> None:
        object.__setattr__(self, "_instance_id", instance_id)
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "_lifecycle_status", lifecycle_status)
        object.__setattr__(self, "_current_stage", current_stage)
        object.__setattr__(self, "_recent_activity", recent_activity)

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def lifecycle_status(self) -> InstanceStatus:
        return self._lifecycle_status

    @property
    def current_stage(self) -> str | None:
        return self._current_stage

    @property
    def recent_activity(self) -> tuple[str, ...]:
        return self._recent_activity

    def __setattr__(self, name: str, value: object) -> None:
        raise FrozenInstanceError(f"cannot set attribute '{name}'")

    def __repr__(self) -> str:
        return (
            f"WorkflowInstanceView(instance_id={self._instance_id!r}, "
            f"run_id={self._run_id!r}, lifecycle_status={self._lifecycle_status!r}, "
            f"current_stage={self._current_stage!r}, recent_activity={self._recent_activity!r})"
        )


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
        tracker_module = import_module("ralph._supervising_tracker")
        return tracker_module.WorkflowInstanceTracker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
