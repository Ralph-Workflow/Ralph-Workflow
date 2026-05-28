"""ProcessTerminationError exception."""

from __future__ import annotations


class ProcessTerminationError(RuntimeError):
    """Raised when a managed process cannot be terminated after escalation.

    Attributes:
        pid: The PID of the process that failed to terminate.
        pgid: The process group ID.
        stage: Which escalation stage failed
            ('graceful_terminate', 'force_kill', 'zombie_detected',
             'access_denied', 'already_gone').
        reason: Human-readable explanation of what went wrong.
        descendant_pids: Optional list of descendant PIDs that could not
            be terminated.
    """

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
