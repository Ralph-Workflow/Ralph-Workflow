"""Workflow instance tracker for orchestration-facing supervision."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.supervising import InstanceStatus, WorkflowInstanceView, instance_view_from_snapshot

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot


class WorkflowInstanceTracker:
    """Owns the stable orchestration identity and updates the immutable view from live snapshots.

    Construct with the stable instance identity before the workflow starts running.
    The tracker initializes an immutable NOT_STARTED view that remains valid
    until the first snapshot arrives.

    Wire to live snapshots using the ``on_snapshot`` callback of
    ``PipelineSubscriber``::

        tracker = WorkflowInstanceTracker(instance_id="work-42")
        subscriber = PipelineSubscriber(
            ...,
            on_snapshot=tracker.update_from_snapshot,
        )
        # Inspect current state:
        view = tracker.view

    The tracker's ``view`` property always reflects the latest snapshot,
    while the ``instance_id`` remains stable from construction time.
    """

    __slots__ = ("_view",)

    _view: WorkflowInstanceView

    def __init__(self, instance_id: str) -> None:
        """Initialize tracker with a stable orchestration identity.

        Args:
            instance_id: Stable identity assigned by the orchestrator.
                Must be a non-empty string. The identity must remain stable
                for the lifetime of this workflow instance.
        """
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
        """Return the latest immutable view of this workflow instance."""
        return self._view

    def update_from_snapshot(self, snapshot: PipelineSnapshot) -> WorkflowInstanceView:
        """Update the immutable view from a live pipeline snapshot.

        Preserves the stable ``instance_id`` assigned at construction.
        Copies ``snapshot.run_id`` into the view's ``run_id`` field when
        the snapshot carries a runtime identity.

        Args:
            snapshot: Latest pipeline snapshot from the live execution.

        Returns:
            The updated immutable WorkflowInstanceView.
        """
        self._view = instance_view_from_snapshot(
            snapshot,
            _instance_id_override=self._view.instance_id,
        )
        return self._view
