"""Asyncio signal bridge for hard-kill on SIGINT.

Uses loop.add_signal_handler() — NOT signal.signal() — to stay compatible
with the asyncio event loop.

First SIGINT: cancels root_task + kills all tracked subprocess process groups
  via ProcessManager.shutdown_all(grace_period_s=0).
Second SIGINT: os._exit(130) immediately (no cleanup)

The bridge.pids set is kept in sync by subscribing to ProcessManager lifecycle
events; callers must not register or deregister PIDs manually.
"""

from __future__ import annotations

import contextlib
import os
import signal
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from ralph.process.manager import ProcessEvent, ProcessStatus, get_process_manager

if TYPE_CHECKING:
    import asyncio


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
) -> None:
    pm = get_process_manager()
    bridge._unsubscribe = pm.register_listener(bridge._on_process_event)

    def _first_sigint() -> None:
        bridge._interrupt_count += 1
        try:
            pm.shutdown_all(grace_period_s=0)
        except Exception:
            logger.warning("ProcessManager.shutdown_all raised during SIGINT")
        for pid in list(bridge.pids):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
        if bridge._connectivity_stop is not None:
            with contextlib.suppress(Exception):
                bridge._connectivity_stop()
        root_task.cancel()
        loop.add_signal_handler(signal.SIGINT, _second_sigint)

    def _second_sigint() -> None:
        os._exit(130)

    loop.add_signal_handler(signal.SIGINT, _first_sigint)


__all__ = ["SignalBridge", "install_signal_handlers"]
