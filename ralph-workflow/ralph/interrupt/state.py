from __future__ import annotations

import threading


class _UserInterruptFlag:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


_USER_INTERRUPT_OCCURRED = _UserInterruptFlag()


def request_user_interrupt() -> None:
    _USER_INTERRUPT_OCCURRED.set()


def user_interrupted_occurred() -> bool:
    return _USER_INTERRUPT_OCCURRED.is_set()


__all__ = ["request_user_interrupt", "user_interrupted_occurred"]
