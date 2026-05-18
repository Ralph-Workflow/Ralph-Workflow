"""LivenessProbe protocol and implementations for aggregate-tree idle evaluation.

The LivenessProbe is an injectable seam so unit tests can fake agent-tree
activity without spawning real processes.
"""

from __future__ import annotations

from ralph.process._default_liveness_probe import DefaultLivenessProbe
from ralph.process._liveness_probe import LivenessProbe
from ralph.process.child_liveness import ChildActivitySnapshot


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
