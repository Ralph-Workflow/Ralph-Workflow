"""Asyncio signal bridge for hard-kill on SIGINT.

Uses loop.add_signal_handler() — NOT signal.signal() — to stay compatible
with the asyncio event loop.

First SIGINT: cancels root_task + kills all tracked subprocess process groups
Second SIGINT: os._exit(130) immediately (no cleanup)
"""

from __future__ import annotations

import contextlib
import os
import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio


@dataclass
class SignalBridge:
    pids: set[int] = field(default_factory=set)
    _interrupt_count: int = field(default=0, init=False)

    def register_pid(self, pid: int) -> None:
        self.pids.add(pid)

    def deregister_pid(self, pid: int) -> None:
        self.pids.discard(pid)


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    root_task: asyncio.Task[object],
    bridge: SignalBridge,
) -> None:
    def _first_sigint() -> None:
        bridge._interrupt_count += 1
        for pid in list(bridge.pids):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
        root_task.cancel()
        loop.add_signal_handler(signal.SIGINT, _second_sigint)

    def _second_sigint() -> None:
        os._exit(130)

    loop.add_signal_handler(signal.SIGINT, _first_sigint)


__all__ = ["SignalBridge", "install_signal_handlers"]
