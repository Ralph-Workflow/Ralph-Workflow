"""Recovery cycle cap: bounded cap on total recovery cycles."""

from __future__ import annotations


class CycleCap:
    """Tracks and enforces the maximum number of recovery cycles.

    A recovery cycle increments when the entire agent chain for a phase
    is exhausted. The cap prevents a persistently-failing handler from
    looping silently forever.
    """

    def __init__(self, cap: int) -> None:
        if cap < 1:
            raise ValueError(f"CycleCap must be >= 1, got {cap}")
        self._cap = cap

    @property
    def cap(self) -> int:
        return self._cap

    def is_exceeded(self, count: int) -> bool:
        """Return True if count >= cap."""
        return count >= self._cap

    def exit_reason(self, count: int, last_category: str, last_reason: str) -> str:
        """Build a descriptive exit reason for when the cap is exceeded."""
        return (
            f"recovery cycle cap {self._cap} exceeded after {count} recoveries; "
            f"last failure category={last_category}: {last_reason}"
        )
