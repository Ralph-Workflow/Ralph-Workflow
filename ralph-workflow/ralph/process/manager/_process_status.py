"""ProcessStatus enum and terminal status constants."""

from __future__ import annotations

from enum import Enum, auto


class ProcessStatus(Enum):
    SPAWNED = auto()
    RUNNING = auto()
    EXITED = auto()
    KILLED = auto()
    FAILED = auto()


_TERMINAL_STATUSES = (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED)
