"""LivenessProbe protocol and implementations for aggregate-tree idle evaluation.

The LivenessProbe is an injectable seam so unit tests can fake agent-tree
activity without spawning real processes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildActivitySnapshot, ChildLivenessRegistry


@runtime_checkable
class LivenessProbe(Protocol):
    """Protocol for checking whether any tracked agent label is still active."""

    def any_agent_active(self, label_prefix: str) -> bool:
        """Return True if any tracked process whose label starts with label_prefix is running."""
        ...

    def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        """Return a freshness-aware snapshot for all children matching scope_prefix."""
        ...


class DefaultLivenessProbe:
    """Production probe: queries the ProcessManager singleton for active labels.

    Accepts an optional ChildLivenessRegistry for freshness-aware child_snapshot().
    When no registry is supplied, child_snapshot() returns a conservative snapshot
    based on ProcessManager labels only (has_process=True/False, no freshness).
    """

    def __init__(self, registry: ChildLivenessRegistry | None = None) -> None:
        self._registry = registry

    def any_agent_active(self, label_prefix: str) -> bool:
        from ralph.process.manager import get_process_manager  # noqa: PLC0415

        return any(
            r.label is not None and r.label.startswith(label_prefix)
            for r in get_process_manager().list_active()
        )

    def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        from ralph.process.child_liveness import ChildActivitySnapshot  # noqa: PLC0415
        from ralph.process.manager import get_process_manager  # noqa: PLC0415

        # Only scan ProcessManager labels when we have a meaningful (non-empty) prefix.
        # An empty prefix would match ALL active processes including the parent itself.
        has_process = False
        active_count = 0
        if scope_prefix:
            active_records = get_process_manager().list_active()
            for r in active_records:
                if r.label is not None and r.label.startswith(scope_prefix):
                    has_process = True
                    active_count += 1

        if self._registry is not None:
            reg_snap = self._registry.snapshot(scope_prefix)
            return ChildActivitySnapshot(
                scope_prefix=scope_prefix,
                has_process=has_process or reg_snap.has_process,
                has_fresh_label=reg_snap.has_fresh_label,
                has_fresh_progress=reg_snap.has_fresh_progress,
                oldest_live_child_seconds=reg_snap.oldest_live_child_seconds,
                active_count=max(active_count, reg_snap.active_count),
                terminal_count=reg_snap.terminal_count,
            )

        return ChildActivitySnapshot(
            scope_prefix=scope_prefix,
            has_process=has_process,
            has_fresh_label=has_process,  # label presence is the only evidence
            has_fresh_progress=False,
            oldest_live_child_seconds=None,
            active_count=active_count,
            terminal_count=0,
        )


class FakeLivenessProbe:
    """Test-only probe that returns a fixed activity answer.

    When ``active_labels`` is provided the probe simulates a specific set of
    active process labels: ``any_agent_active(prefix)`` returns True only when
    at least one label in ``active_labels`` starts with ``prefix``.  This lets
    tests distinguish between related and unrelated agent workers.

    When ``active_labels`` is None the probe falls back to the flat ``active``
    flag (existing behaviour, unchanged).

    When ``snapshot`` is provided, child_snapshot() returns it for any prefix.
    """

    def __init__(
        self,
        *,
        active: bool = False,
        active_labels: frozenset[str] | None = None,
        snapshot: ChildActivitySnapshot | None = None,
    ) -> None:
        self._active = active
        self._active_labels = active_labels
        self._snapshot = snapshot

    def any_agent_active(self, label_prefix: str) -> bool:
        if self._active_labels is not None:
            return any(label.startswith(label_prefix) for label in self._active_labels)
        return self._active

    def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        from ralph.process.child_liveness import ChildActivitySnapshot  # noqa: PLC0415

        if self._snapshot is not None:
            return self._snapshot
        # For empty prefix with label-based matching, don't scan labels.
        # Mirrors DefaultLivenessProbe which skips label scanning for empty
        # scope_prefix to avoid false matches against the parent process.
        if not scope_prefix and self._active_labels is not None:
            active = False
        else:
            active = self.any_agent_active(scope_prefix)
        return ChildActivitySnapshot(
            scope_prefix=scope_prefix,
            has_process=active,
            has_fresh_label=active,
            has_fresh_progress=active,
            oldest_live_child_seconds=None,
            active_count=1 if active else 0,
            terminal_count=0,
        )


__all__ = [
    "DefaultLivenessProbe",
    "FakeLivenessProbe",
    "LivenessProbe",
]
