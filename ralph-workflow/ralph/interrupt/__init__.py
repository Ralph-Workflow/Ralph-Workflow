"""Interrupt-handling helpers for unattended runs.

The orchestrator uses this module to install a process-wide SIGINT handler and
record whether a user interruption has been requested. The state is intentionally
simple so both CLI code and long-running loops can check it safely.
"""

from __future__ import annotations

from ralph.interrupt.asyncio_bridge import install_signal_handlers as install_signal_handlers
from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE as INTERRUPT_EXIT_CODE,
)
from ralph.interrupt.controller import (
    InterruptController as InterruptController,
)
from ralph.interrupt.controller import (
    controller_from_process_manager as controller_from_process_manager,
)
from ralph.interrupt.state import (
    request_user_interrupt as request_user_interrupt,
)
from ralph.interrupt.state import (
    user_interrupted_occurred as user_interrupted_occurred,
)

__all__ = [
    "INTERRUPT_EXIT_CODE",
    "InterruptController",
    "controller_from_process_manager",
    "install_signal_handlers",
    "request_user_interrupt",
    "user_interrupted_occurred",
]
