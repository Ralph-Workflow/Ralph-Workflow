"""InvalidTransitionError — raised when an event is invalid in the current state."""

from __future__ import annotations


class InvalidTransitionError(Exception):
    """Raised when an event is invalid in the current state."""


__all__ = ["InvalidTransitionError"]
