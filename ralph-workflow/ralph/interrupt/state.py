"""Shared process-local interrupt state helpers."""

from __future__ import annotations

import threading

__all__ = ["request_user_interrupt", "user_interrupted_occurred"]

_USER_INTERRUPT_OCCURRED = threading.Event()


def request_user_interrupt() -> None:
    """Record that a user interrupt has been requested."""

    _USER_INTERRUPT_OCCURRED.set()


def user_interrupted_occurred() -> bool:
    """Return ``True`` if any user interrupt has occurred in this process."""

    return _USER_INTERRUPT_OCCURRED.is_set()
