"""RebaseEvent — events that drive transitions in the RebaseStateMachine."""

from __future__ import annotations

from enum import Enum


class RebaseEvent(Enum):
    """Events that drive transitions in the ``RebaseStateMachine``."""

    START_REBASE = "start_rebase"
    CONFLICT_DETECTED = "conflict_detected"
    START_RESOLUTION = "start_resolution"
    RESOLVE_CONFLICT = "resolve_conflict"
    CONTINUE = "continue"
    COMPLETE = "complete"
    ABORT = "abort"


__all__ = ["RebaseEvent"]
