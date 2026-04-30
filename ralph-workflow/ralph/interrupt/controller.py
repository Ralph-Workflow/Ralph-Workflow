"""Dependency-injected interrupt orchestration helpers.

This module centralizes what should happen when Ralph receives a user interrupt:
record it, stop optional connectivity waits, try a graceful shutdown first, and
escalate to a forced kill plus hard exit on a second interrupt. Keeping these
actions behind an injectable controller makes the behavior testable without
real signals.
"""

from __future__ import annotations

import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.interrupt.state import request_user_interrupt
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import FrameType
    from typing import Protocol

    from ralph.process.manager import ProcessManager

    type SignalHandler = Callable[[int, FrameType | None], object] | int | None

    class SignalGetter(Protocol):
        def __call__(self, signalnum: int, /) -> SignalHandler: ...

    class SignalSetter(Protocol):
        def __call__(self, signalnum: int, handler: SignalHandler, /) -> SignalHandler: ...


INTERRUPT_EXIT_CODE = 130


@dataclass(frozen=True)
class InterruptController:
    """Coordinate graceful and forced interrupt handling through injected seams."""

    shutdown_all: Callable[[float], None]
    record_interrupt: Callable[[], None] = request_user_interrupt
    stop_connectivity: Callable[[], None] | None = None
    kill_process_group: Callable[[int, int], None] | None = None
    hard_exit: Callable[[int], None] | None = None

    def begin_interrupt(self, *, grace_period_s: float) -> None:
        """Record the interrupt and attempt graceful tracked-process shutdown."""
        self.record_interrupt()
        if self.stop_connectivity is not None:
            with suppress(Exception):
                self.stop_connectivity()
        self.shutdown_all(grace_period_s)

    def force_interrupt(self, *, bridge_pids: Iterable[int] = ()) -> None:
        """Escalate to immediate tracked-process termination."""
        self.record_interrupt()
        if self.stop_connectivity is not None:
            with suppress(Exception):
                self.stop_connectivity()
        self.shutdown_all(0)
        kill_process_group = self.kill_process_group or os.killpg
        for pid in bridge_pids:
            with suppress(ProcessLookupError, PermissionError):
                kill_process_group(pid, signal.SIGKILL)

    def force_exit(self, *, bridge_pids: Iterable[int] = ()) -> None:
        """Force-kill tracked work and exit with the canonical interrupt code."""
        self.force_interrupt(bridge_pids=bridge_pids)
        hard_exit = self.hard_exit or os._exit
        hard_exit(INTERRUPT_EXIT_CODE)


def install_force_kill_handler(
    on_force_interrupt: Callable[[], None],
    *,
    signal_getter: SignalGetter = signal.getsignal,
    signal_setter: SignalSetter = signal.signal,
) -> Callable[[], None]:
    """Install a temporary SIGINT handler that escalates to forced termination."""
    previous = signal_getter(signal.SIGINT)

    def _handler(signum: int, frame: object) -> None:
        del signum, frame
        on_force_interrupt()

    signal_setter(signal.SIGINT, _handler)

    def _restore() -> None:
        signal_setter(signal.SIGINT, previous)

    return _restore


def controller_from_process_manager(
    *,
    process_manager: ProcessManager | None = None,
    stop_connectivity: Callable[[], None] | None = None,
    record_interrupt: Callable[[], None] = request_user_interrupt,
    kill_process_group: Callable[[int, int], None] | None = None,
    hard_exit: Callable[[int], None] | None = None,
) -> InterruptController:
    """Build an :class:`InterruptController` from a ProcessManager instance."""
    manager = process_manager or get_process_manager()

    def _shutdown_all(grace_period_s: float) -> None:
        manager.shutdown_all(grace_period_s=grace_period_s)

    return InterruptController(
        shutdown_all=_shutdown_all,
        record_interrupt=record_interrupt,
        stop_connectivity=stop_connectivity,
        kill_process_group=kill_process_group,
        hard_exit=hard_exit,
    )


__all__ = [
    "INTERRUPT_EXIT_CODE",
    "InterruptController",
    "controller_from_process_manager",
    "install_force_kill_handler",
]
