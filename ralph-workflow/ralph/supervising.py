"""Trackable workflow instance model for orchestration use cases.

Exposes the minimum product-facing information an external orchestrator needs
to monitor a running Ralph Workflow instance: stable identity, lifecycle status,
current pipeline stage, and recent operational activity.
"""

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


# =============================================================================
# WorkflowInstanceView
# =============================================================================


class WorkflowInstanceView:
    """Immutable view of a single Ralph Workflow instance for orchestration.

    Attributes:
        instance_id: Stable orchestration identity assigned at tracker construction,
            or the runtime run_id when projected directly from a snapshot.
            For tracker-based supervision, this is always a non-empty str.
        run_id: Optional runtime identifier copied from the live pipeline snapshot.
            This may be None before startup or when the underlying system does not
            assign a runtime identity. It is separate from the stable instance_id
            so that a supervising orchestrator can track the same instance across
            restarts or reconnects without confusion.
        lifecycle_status: Observable lifecycle state of the instance.
        current_stage: Active pipeline stage name, or None when no stage is active
            (including before startup, after terminal states, and when phase is unset).
        recent_activity: Recent operational output, ordered oldest to newest.
    """

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


# =============================================================================
# Snapshot projection helpers
# =============================================================================


def instance_view_from_snapshot(
    snapshot: PipelineSnapshot,
    *,
    _instance_id_override: str | None = None,
) -> WorkflowInstanceView:
    """Project a PipelineSnapshot into a WorkflowInstanceView.

    When called with ``_instance_id_override``, that stable identity is
    used and ``snapshot.run_id`` is copied to the view's ``run_id`` field.
    This form is used internally by ``WorkflowInstanceTracker`` to preserve
    the orchestrator-assigned identity while exposing the runtime ``run_id``
    separately.

    When called without an identity override (the default), the view's
    ``instance_id`` is taken directly from ``snapshot.run_id``. This form
    is only valid when ``snapshot.run_id`` is not None. If ``snapshot.run_id``
    is None and no override is provided, a ``ValueError`` is raised because
    the supervising contract requires a stable orchestrator-facing identity.

    Args:
        snapshot: The pipeline snapshot to project.
        _instance_id_override: Stable identity to use instead of snapshot.run_id.
            Should be supplied by WorkflowInstanceTracker or when the caller
            needs to project a snapshot without a runtime identity.

    Raises:
        ValueError: If ``snapshot.run_id`` is None and no ``_instance_id_override``
            is provided. The supervising contract requires a stable identity.
    """
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
    if snapshot.waiting_status_line is not None:
        lines.append(snapshot.waiting_status_line)
    elif snapshot.last_activity_line is not None:
        lines.append(snapshot.last_activity_line)
    return tuple(lines)


def __getattr__(name: str) -> object:
    if name == "WorkflowInstanceTracker":
        from ralph._supervising_tracker import WorkflowInstanceTracker

        return WorkflowInstanceTracker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
