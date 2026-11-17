from __future__ import annotations

import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.terminal_restore import restore_terminal as _restore_terminal
from ralph.interrupt.state import request_user_interrupt
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import FrameType

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter
    from ralph.process.manager import ProcessManager


_DEFAULT_SIGNAL_GETTER = cast("SignalGetter", signal.getsignal)
_DEFAULT_SIGNAL_SETTER = cast("SignalSetter", signal.signal)

INTERRUPT_EXIT_CODE = 130
_INTERRUPT_EXIT_CODE_REQUIRED: int = 130
if INTERRUPT_EXIT_CODE != _INTERRUPT_EXIT_CODE_REQUIRED:
    raise RuntimeError(
        f"INTERRUPT_EXIT_CODE must be {_INTERRUPT_EXIT_CODE_REQUIRED} (got {INTERRUPT_EXIT_CODE})"
    )


@dataclass(frozen=True)
class InterruptController:
    shutdown_all: Callable[[float], None]
    record_interrupt: Callable[[], None] = request_user_interrupt
    stop_connectivity: Callable[[], None] | None = None
    kill_process_group: Callable[[int, int], None] | None = None
    hard_exit: Callable[[int], None] | None = None
    restore_terminal: Callable[[], None] | None = None
    shutdown_all_for_label: Callable[[str, float], None] | None = None

    def begin_interrupt(
        self,
        *,
        grace_period_s: float,
        kill_label: str = "",
    ) -> None:
        self.record_interrupt()
        if self.stop_connectivity is not None:
            with suppress(Exception):
                self.stop_connectivity()
        if kill_label and self.shutdown_all_for_label is not None:
            self.shutdown_all_for_label(kill_label, grace_period_s)
        else:
            self.shutdown_all(grace_period_s)

    def force_interrupt(
        self,
        *,
        bridge_pgids: Iterable[int] = (),
        **kwargs: object,
    ) -> None:
        bridge_pids_legacy = cast("Iterable[int]", kwargs.pop("bridge_pids", ()))
        if bridge_pids_legacy:
            logger.warning("bridge_pids is deprecated; pass bridge_pgids instead")
        del bridge_pgids, bridge_pids_legacy
        self.record_interrupt()
        if self.stop_connectivity is not None:
            with suppress(Exception):
                self.stop_connectivity()
        self.shutdown_all(0)

    def force_exit(
        self,
        *,
        bridge_pgids: Iterable[int] = (),
        **kwargs: object,
    ) -> None:
        bridge_pids_legacy = cast("Iterable[int]", kwargs.pop("bridge_pids", ()))
        if bridge_pids_legacy:
            logger.warning("bridge_pids is deprecated; pass bridge_pgids instead")
        pgids: Iterable[int] = list(bridge_pgids) if bridge_pgids else list(bridge_pids_legacy)
        self.force_interrupt(bridge_pgids=pgids)
        restore = self.restore_terminal or _restore_terminal
        restore()
        hard_exit = self.hard_exit or os._exit
        hard_exit(INTERRUPT_EXIT_CODE)


def install_force_kill_handler(
    on_force_interrupt: Callable[[], None],
    *,
    signal_getter: SignalGetter = _DEFAULT_SIGNAL_GETTER,
    signal_setter: SignalSetter = _DEFAULT_SIGNAL_SETTER,
    signum: int = signal.SIGINT,
) -> Callable[[], None]:
    previous = signal_getter(signum)

    def _handler(received_signum: int, frame: FrameType | None) -> None:
        del received_signum, frame
        on_force_interrupt()

    signal_setter(signum, _handler)

    def _restore() -> None:
        signal_setter(signum, previous)

    return _restore


def controller_from_process_manager(
    *,
    process_manager: ProcessManager | None = None,
    stop_connectivity: Callable[[], None] | None = None,
    record_interrupt: Callable[[], None] = request_user_interrupt,
    kill_process_group: Callable[[int, int], None] | None = None,
    hard_exit: Callable[[int], None] | None = None,
) -> InterruptController:
    manager = process_manager or get_process_manager()

    def _shutdown_all(grace_period_s: float) -> None:
        manager.shutdown_all(grace_period_s=grace_period_s)

    def _shutdown_all_for_label(label_prefix: str, grace_period_s: float) -> None:
        manager.shutdown_all_for_label(label_prefix, grace_period_s=grace_period_s)

    return InterruptController(
        shutdown_all=_shutdown_all,
        shutdown_all_for_label=_shutdown_all_for_label,
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
