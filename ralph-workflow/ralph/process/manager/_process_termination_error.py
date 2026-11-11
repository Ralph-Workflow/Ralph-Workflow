
from __future__ import annotations


class ProcessTerminationError(RuntimeError):

    def __init__(
        self,
        pid: int,
        pgid: int,
        *,
        stage: str = "force_kill",
        reason: str = "Process could not be terminated",
        descendant_pids: list[int] | None = None,
    ) -> None:
        self.pid = pid
        self.pgid = pgid
        self.stage = stage
        self.reason = reason
        self.descendant_pids = descendant_pids or []
        msg = f"Process {pid} (pgid {pgid}) [{stage}]: {reason}"
        if self.descendant_pids:
            msg += f" | descendant_pids={self.descendant_pids}"
        super().__init__(msg)
