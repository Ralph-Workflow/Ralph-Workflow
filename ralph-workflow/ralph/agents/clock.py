"""Clock protocol for the agent timeout subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import threading

__all__ = ["Clock"]


@runtime_checkable
class Clock(Protocol):
    """Protocol for wall-clock operations used by the timeout subsystem."""

    def monotonic(self) -> float:
        """Return current monotonic time in seconds."""
        ...  # pragma: no cover

    def sleep(self, seconds: float) -> None:
        """Pause execution for the given number of seconds."""
        ...  # pragma: no cover

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        """Wait up to seconds for event to be set.

        Returns True if the event was set during the wait, False on timeout.
        Production: uses event.wait() so line arrivals wake the poll loop immediately.
        Test: advances logical time by seconds and checks event state (no real wait).
        """
        ...  # pragma: no cover
