"""ProcessTerminationError exception."""

from __future__ import annotations


class ProcessTerminationError(RuntimeError):
    def __init__(self, pid: int, pgid: int) -> None:
        self.pid = pid
        self.pgid = pgid
        super().__init__(f"Process {pid} (pgid {pgid}) could not be terminated")
