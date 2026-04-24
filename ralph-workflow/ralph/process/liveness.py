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
    """Test-only probe that returns a fixed activity answer regardless of label."""

    def __init__(self, *, active: bool = False) -> None:
        self._active = active

    def any_agent_active(self, label_prefix: str) -> bool:
        del label_prefix
        return self._active


__all__ = [
    "DefaultLivenessProbe",
    "FakeLivenessProbe",
    "LivenessProbe",
]
