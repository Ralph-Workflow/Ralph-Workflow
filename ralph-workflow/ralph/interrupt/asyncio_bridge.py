"""Asyncio signal bridge for graceful-then-forced SIGINT handling.

Uses ``loop.add_signal_handler()`` — not ``signal.signal()`` — to stay
compatible with the asyncio event loop.

Signal handling contract:

* First ``SIGINT`` records the interrupt, requests graceful tracked-process
  shutdown, and cancels ``root_task``.
* Second ``SIGINT`` force-kills tracked child processes and exits with code 130.

``bridge.pids`` stays synchronized by subscribing to ProcessManager lifecycle
Events, so callers must not register or deregister PIDs manually.

The interrupt dispatch is routed through :class:`InterruptDispatcher`
so the same wiring lives in both the sync ``handle_keyboard_interrupt``
path and this asyncio path. The ``controller`` parameter is
type-broadened to accept either an ``InterruptController`` or an
already-built ``InterruptDispatcher``; ``install_signal_handlers``
discriminates by ``isinstance`` inside the function body. The
parameter name is preserved for backward compatibility — the
broadening is type-only.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
)
from ralph.process.manager import ProcessEvent, ProcessStatus, get_process_manager

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from ralph.interrupt.controller import InterruptController


@dataclass
class SignalBridge:
    """Bridge that routes OS signals to asyncio task cancellation and process cleanup."""

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
    controller: InterruptController | InterruptDispatcher | None = None,
) -> None:
    """Register SIGINT and SIGTERM handlers that cancel ``root_task`` and forward to child PIDs.

    The fourth argument is type-broadened to accept an
    :class:`InterruptController` (legacy) OR an
    :class:`InterruptDispatcher` (new). Discrimination is by
    ``isinstance`` inside the body. When a controller is passed, the
    implementation synthesizes a dispatcher that forwards the
    controller's ``kill_process_group`` and ``hard_exit`` so the
    controller's injected exit callable is the one invoked on
    ``_second_sigint`` (PA-019).
    """
    pm = get_process_manager()
    bridge._unsubscribe = pm.register_listener(bridge._on_process_event)
    if controller is None:
        active_dispatcher: InterruptDispatcher = dispatcher_from_process_manager(
            process_manager=pm,
            stop_connectivity=bridge._connectivity_stop,
        )
    elif isinstance(controller, InterruptDispatcher):
        active_dispatcher = controller
    else:
        # Raw InterruptController passed; wrap in a dispatcher so the
        # kill_label propagation and block=True behavior are uniform.
        # Thread kill_process_group and hard_exit through so the
        # controller's injected exit callable is the one invoked on
        # _second_sigint (PA-019). The dispatcher factory creates a
        # fresh controller with the same injection seams; we then
        # rebind ``controller`` to the passed controller so the
        # wrapping methods operate on the original (preserving
        # record_interrupt, stop_connectivity, etc.).
        wrapped = dispatcher_from_process_manager(
            process_manager=pm,
            stop_connectivity=bridge._connectivity_stop,
            record_interrupt=controller.record_interrupt,
            kill_process_group=controller.kill_process_group,
            hard_exit=controller.hard_exit,
        )
        object.__setattr__(wrapped, "controller", controller)
        active_dispatcher = wrapped

    def _first_sigint() -> None:
        bridge._interrupt_count += 1
        try:
            active_dispatcher.begin_interrupt(
                grace_period_s=pm.policy.default_grace_period_s,
            )
        except Exception:
            logger.warning("Interrupt dispatcher raised during SIGINT")
        root_task.cancel()
        loop.add_signal_handler(signal.SIGINT, _second_sigint)

    def _second_sigint() -> None:
        active_dispatcher.force_exit(bridge_pids=list(bridge.pids))

    loop.add_signal_handler(signal.SIGINT, _first_sigint)


__all__ = ["SignalBridge", "install_signal_handlers"]
