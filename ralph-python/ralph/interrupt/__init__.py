"""Interrupt-handling helpers for unattended runs.

The orchestrator uses this module to install a process-wide SIGINT handler and
record whether a user interruption has been requested. The state is intentionally
simple so both CLI code and long-running loops can check it safely.
"""

from __future__ import annotations

import signal
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType

__all__ = [
    "request_user_interrupt",
    "setup_interrupt_handler",
    "user_interrupted_occurred",
]

_USER_INTERRUPT_OCCURRED = threading.Event()
_HANDLER_LOCK = threading.Lock()
_HANDLER_INSTALLED = threading.Event()


def _sigint_handler(signum: int, frame: FrameType | None) -> None:
    """Handle SIGINT by recording that the user interrupted."""

    request_user_interrupt()


def setup_interrupt_handler() -> None:
    """Install a SIGINT handler that records user interrupts."""

    with _HANDLER_LOCK:
        if _HANDLER_INSTALLED.is_set():
            return

        signal.signal(signal.SIGINT, _sigint_handler)
        _HANDLER_INSTALLED.set()


def request_user_interrupt() -> None:
    """Record that a user interrupt has been requested."""

    _USER_INTERRUPT_OCCURRED.set()


def user_interrupted_occurred() -> bool:
    """Return ``True`` if any user interrupt has occurred in this process."""

    return _USER_INTERRUPT_OCCURRED.is_set()
