"""Asyncio signal bridge for graceful-then-forced SIGINT handling.

Uses ``loop.add_signal_handler()`` — not ``signal.signal()`` — to stay
compatible with the asyncio event loop.

Signal handling contract:

* First ``SIGINT`` records the interrupt, requests graceful tracked-process
  shutdown, and cancels ``root_task``.
* Second ``SIGINT`` force-kills tracked child processes and exits with code 130.

``bridge.pids`` stays synchronized by subscribing to ProcessManager lifecycle
Events, so callers must not register or deregister PIDs manually.
"""

from __future__ import annotations

import contextlib
import os
import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from ralph.interrupt.controller import InterruptController, controller_from_process_manager
from ralph.process.manager import ProcessEvent, ProcessStatus, get_process_manager

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable


@dataclass
class SignalBridge:
    pids: set[int] = field(default_factory=set)
    _interrupt_count: int = field(default=0, init=False)
    _unsubscribe: object = field(default=None, init=False)
    _connectivity_stop: Callable[[], None] | None = field(default=None, init=False)

    def register_pid(self, pid: int) -> None:
        self.pids.add(pid)

    def deregister_pid(self, pid: int) -> None:
        self.pids.discard(pid)

    def _on_process_event(self, event: ProcessEvent) -> None:
        if event.new_status == ProcessStatus.RUNNING:
            self.pids.add(event.record.pid)
        elif event.new_status in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
            self.pids.discard(event.record.pid)


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    root_task: asyncio.Task[object],
    bridge: SignalBridge,
    controller: InterruptController | None = None,
) -> None:
    pm = get_process_manager()
    bridge._unsubscribe = pm.register_listener(bridge._on_process_event)
    active_controller = controller or controller_from_process_manager(
        process_manager=pm,
        stop_connectivity=bridge._connectivity_stop,
    )

    def _first_sigint() -> None:
        bridge._interrupt_count += 1
        try:
            active_controller.begin_interrupt(grace_period_s=pm.policy.default_grace_period_s)
        except Exception:
            logger.warning("Interrupt controller raised during SIGINT")
        root_task.cancel()
        loop.add_signal_handler(signal.SIGINT, _second_sigint)

    def _second_sigint() -> None:
        active_controller.force_exit(bridge_pids=list(bridge.pids))

    loop.add_signal_handler(signal.SIGINT, _first_sigint)


__all__ = ["SignalBridge", "install_signal_handlers"]
