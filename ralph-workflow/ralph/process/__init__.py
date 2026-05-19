"""Process management package — single source of truth for all child processes.

Every subprocess Ralph spawns flows through :class:`ProcessManager`.  The
manager records lifecycle transitions, emits observable events, and owns
escalating termination via psutil for cross-platform process-tree teardown
(Linux, macOS, and Windows). No POSIX-only APIs are used.
"""

from ralph.process.child_liveness import (
    ChildActivitySnapshot,
    ChildLivenessRecord,
    ChildLivenessRegistry,
)
from ralph.process.manager import (
    ManagedAsyncProcess,
    ManagedProcess,
    ManagedPtyProcess,
    ProcessEvent,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
    ProcessTerminationError,
    PtySpawnOptions,
    SpawnOptions,
    get_process_manager,
    process_phase_scope,
    reset_process_manager,
)

__all__ = [
    "ChildActivitySnapshot",
    "ChildLivenessRecord",
    "ChildLivenessRegistry",
    "ManagedAsyncProcess",
    "ManagedProcess",
    "ManagedPtyProcess",
    "ProcessEvent",
    "ProcessManager",
    "ProcessManagerPolicy",
    "ProcessRecord",
    "ProcessStatus",
    "ProcessTerminationError",
    "PtySpawnOptions",
    "SpawnOptions",
    "get_process_manager",
    "process_phase_scope",
    "reset_process_manager",
]
