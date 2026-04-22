"""Process management package — single source of truth for all child processes.

Every subprocess Ralph spawns flows through :class:`ProcessManager`.  The
manager records lifecycle transitions, emits observable events, and owns
escalating termination via psutil for cross-platform process-tree teardown
(Linux, macOS, and Windows). No POSIX-only APIs are used.
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
    process_phase_scope,
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
    "process_phase_scope",
    "reset_process_manager",
]
