"""Protocol for checking liveness of agent processes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildActivitySnapshot


@runtime_checkable
class LivenessProbe(Protocol):
    """Protocol for checking whether any tracked agent label is still active."""

    def any_agent_active(self, label_prefix: str) -> bool:
        """Return True if any tracked process whose label starts
        with label_prefix is running."""
        ...

    def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        """Return a freshness-aware snapshot for all children matching scope_prefix."""
        ...
