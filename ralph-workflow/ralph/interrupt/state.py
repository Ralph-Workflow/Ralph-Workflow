from __future__ import annotations

import threading

_USER_INTERRUPT_OCCURRED = threading.Event()


def request_user_interrupt() -> None:
    _USER_INTERRUPT_OCCURRED.set()


def user_interrupted_occurred() -> bool:
    return _USER_INTERRUPT_OCCURRED.is_set()


__all__ = ["request_user_interrupt", "user_interrupted_occurred"]
