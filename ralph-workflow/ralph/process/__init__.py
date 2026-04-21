"""Process management package — single source of truth for all child processes.

Every subprocess Ralph spawns flows through :class:`ProcessManager`.  The
manager records lifecycle transitions, emits observable events, and owns
escalating termination (SIGTERM → SIGKILL of the process group).
"""

from ralph.process.manager import (
    ManagedAsyncProcess,
    ManagedProcess,
    ProcessEvent,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
    ProcessTerminationError,
    get_process_manager,
    reset_process_manager,
)

__all__ = [
    "ManagedAsyncProcess",
    "ManagedProcess",
    "ProcessEvent",
    "ProcessManager",
    "ProcessManagerPolicy",
    "ProcessRecord",
    "ProcessStatus",
    "ProcessTerminationError",
    "get_process_manager",
    "reset_process_manager",
]
