"""LivenessProbe protocol and implementations for aggregate-tree idle evaluation.

The LivenessProbe is an injectable seam so unit tests can fake agent-tree
activity without spawning real processes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LivenessProbe(Protocol):
    """Protocol for checking whether any tracked agent label is still active."""

    def any_agent_active(self, label_prefix: str) -> bool:
        """Return True if any tracked process whose label starts with label_prefix is running."""
        ...


class DefaultLivenessProbe:
    """Production probe: queries the ProcessManager singleton for active labels."""

    def any_agent_active(self, label_prefix: str) -> bool:
        from ralph.process.manager import get_process_manager  # noqa: PLC0415

        return any(
            r.label is not None and r.label.startswith(label_prefix)
            for r in get_process_manager().list_active()
        )


class FakeLivenessProbe:
    """Test-only probe that returns a fixed activity answer.

    When ``active_labels`` is provided the probe simulates a specific set of
    active process labels: ``any_agent_active(prefix)`` returns True only when
    at least one label in ``active_labels`` starts with ``prefix``.  This lets
    tests distinguish between related and unrelated agent workers.

    When ``active_labels`` is None the probe falls back to the flat ``active``
    flag (existing behaviour, unchanged).
    """

    def __init__(
        self,
        *,
        active: bool = False,
        active_labels: frozenset[str] | None = None,
    ) -> None:
        self._active = active
        self._active_labels = active_labels

    def any_agent_active(self, label_prefix: str) -> bool:
        if self._active_labels is not None:
            return any(label.startswith(label_prefix) for label in self._active_labels)
        return self._active


__all__ = [
    "DefaultLivenessProbe",
    "FakeLivenessProbe",
    "LivenessProbe",
]
