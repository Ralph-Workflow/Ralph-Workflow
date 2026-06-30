"""Lifecycle states for a tracked subprocess."""

from __future__ import annotations

from enum import Enum, auto


class ProcessStatus(Enum):
    """Finite-state machine states for a managed child process."""

    SPAWNED = auto()
    RUNNING = auto()
    EXITED = auto()
    KILLED = auto()
    FAILED = auto()


_TERMINAL_STATUSES = (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED)
