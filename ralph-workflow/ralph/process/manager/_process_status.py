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


def is_terminal_status(status: ProcessStatus) -> bool:
    """Return True when ``status`` is a terminal lifecycle state.

    Terminal states are the ones a record can never leave: ``EXITED``,
    ``KILLED`` and ``FAILED``.  Callers that need to know whether a
    process record has finished use this predicate instead of reaching
    for the internal status tuple.
    """
    return status in _TERMINAL_STATUSES
