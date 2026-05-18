"""ProcessManager — single source of truth for every child process Ralph spawns."""

from ralph.process.manager._managed_async_process import ManagedAsyncProcess
from ralph.process.manager._managed_process import ManagedProcess
from ralph.process.manager._managed_pty_process import ManagedPtyProcess
from ralph.process.manager._process_event import ProcessEvent
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.manager._process_record import ProcessRecord
from ralph.process.manager._process_status import ProcessStatus
from ralph.process.manager._process_termination_error import ProcessTerminationError
from ralph.process.manager._pty_spawn_options import PtySpawnOptions
from ralph.process.manager._singleton import (
    get_process_manager,
    process_phase_scope,
    reset_process_manager,
)
from ralph.process.manager._spawn_options import SpawnOptions

__all__ = [
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
