"""Process management package — single source of truth for all child processes.

Every subprocess Ralph spawns flows through :class:`ProcessManager`.  The
manager records lifecycle transitions, emits observable events, and owns
escalating termination via psutil for cross-platform process-tree teardown
(Linux, macOS, and Windows). No POSIX-only APIs are used.
"""

from ralph.process._spawn_env import (
    MALLOC_DEBUG_NOISE_VARS,
    child_env_for_spawn,
    sanitize_process_environment,
    strip_malloc_debug_noise,
)
from ralph.process._spawn_validation import validate_spawn_arguments
from ralph.process.child_liveness import (
    ChildActivitySnapshot,
    ChildLivenessRecord,
    ChildLivenessRegistry,
    ChildLivenessSubagentPidSource,
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
    install_process_manager,
    is_terminal_status,
    process_phase_scope,
    reset_process_manager,
    shutdown_process_manager,
)
from ralph.process.pty import PtyProcess
from ralph.process.teardown import (
    DefaultProcessTeardown,
    ProcessTeardown,
    teardown_subtree,
)

__all__ = [
    "MALLOC_DEBUG_NOISE_VARS",
    "ChildActivitySnapshot",
    "ChildLivenessRecord",
    "ChildLivenessRegistry",
    "ChildLivenessSubagentPidSource",
    "DefaultProcessTeardown",
    "ManagedAsyncProcess",
    "ManagedProcess",
    "ManagedPtyProcess",
    "ProcessEvent",
    "ProcessManager",
    "ProcessManagerPolicy",
    "ProcessRecord",
    "ProcessStatus",
    "ProcessTeardown",
    "ProcessTerminationError",
    "PtyProcess",
    "PtySpawnOptions",
    "SpawnOptions",
    "child_env_for_spawn",
    "get_process_manager",
    "install_process_manager",
    "is_terminal_status",
    "process_phase_scope",
    "reset_process_manager",
    "sanitize_process_environment",
    "shutdown_process_manager",
    "strip_malloc_debug_noise",
    "teardown_subtree",
    "validate_spawn_arguments",
]
