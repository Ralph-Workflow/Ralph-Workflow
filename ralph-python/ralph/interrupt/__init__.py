"""Signal handling utilities and user interrupt helpers."""

from __future__ import annotations

import signal
import threading
from types import FrameType

__all__ = [
    "setup_interrupt_handler",
    "request_user_interrupt",
    "user_interrupted_occurred",
]

_USER_INTERRUPT_OCCURRED = threading.Event()
_HANDLER_LOCK = threading.Lock()
_HANDLER_INSTALLED = False


def _sigint_handler(signum: int, frame: FrameType | None) -> None:
    """Handle SIGINT by recording that the user interrupted."""

    request_user_interrupt()


def setup_interrupt_handler() -> None:
    """Install a SIGINT handler that records user interrupts."""

    global _HANDLER_INSTALLED

    with _HANDLER_LOCK:
        if _HANDLER_INSTALLED:
            return

        signal.signal(signal.SIGINT, _sigint_handler)
        _HANDLER_INSTALLED = True


def request_user_interrupt() -> None:
    """Record that a user interrupt has been requested."""

    _USER_INTERRUPT_OCCURRED.set()


def user_interrupted_occurred() -> bool:
    """Return ``True`` if any user interrupt has occurred in this process."""

    return _USER_INTERRUPT_OCCURRED.is_set()
