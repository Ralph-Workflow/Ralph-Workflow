from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.supervising import InstanceStatus, WorkflowInstanceView, instance_view_from_snapshot

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot


class WorkflowInstanceTracker:
    __slots__ = ("_view",)

    _view: WorkflowInstanceView

    def __init__(self, instance_id: str) -> None:
        if not instance_id:
            raise ValueError("instance_id must be a non-empty string")
        self._view = WorkflowInstanceView(
            instance_id=instance_id,
            run_id=None,
            lifecycle_status=InstanceStatus.NOT_STARTED,
            current_stage=None,
            recent_activity=(),
        )

    @property
    def view(self) -> WorkflowInstanceView:
        return self._view

    def update_from_snapshot(self, snapshot: PipelineSnapshot) -> WorkflowInstanceView:
        self._view = instance_view_from_snapshot(
            snapshot,
            _instance_id_override=self._view.instance_id,
        )
        return self._view
