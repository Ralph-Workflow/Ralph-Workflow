
from __future__ import annotations

from ralph.process._default_liveness_probe import DefaultLivenessProbe
from ralph.process._liveness_probe import LivenessProbe
from ralph.process.child_liveness import ChildActivitySnapshot


class FakeLivenessProbe:

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
