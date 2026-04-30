"""Interrupt-handling helpers for unattended runs.

The orchestrator uses this module to install a process-wide SIGINT handler and
record whether a user interruption has been requested. The state is intentionally
simple so both CLI code and long-running loops can check it safely.
"""

from __future__ import annotations

import threading

from ralph.interrupt.asyncio_bridge import install_signal_handlers as install_signal_handlers
from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE as INTERRUPT_EXIT_CODE,
    InterruptController as InterruptController,
    controller_from_process_manager as controller_from_process_manager,
)

__all__ = [
    "INTERRUPT_EXIT_CODE",
    "InterruptController",
    "controller_from_process_manager",
    "install_signal_handlers",
    "request_user_interrupt",
    "user_interrupted_occurred",
]

_USER_INTERRUPT_OCCURRED = threading.Event()


def request_user_interrupt() -> None:
    """Record that a user interrupt has been requested."""

    _USER_INTERRUPT_OCCURRED.set()


def user_interrupted_occurred() -> bool:
    """Return ``True`` if any user interrupt has occurred in this process."""

    return _USER_INTERRUPT_OCCURRED.is_set()
