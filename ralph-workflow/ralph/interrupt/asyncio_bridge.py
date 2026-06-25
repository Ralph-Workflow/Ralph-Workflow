"""Asyncio signal bridge for graceful-then-forced SIGINT handling.

Uses ``loop.add_signal_handler()`` — not ``signal.signal()`` — to stay
compatible with the asyncio event loop.

Signal handling contract:

* First ``SIGINT`` synchronously cancels ``root_task`` and swaps in
  the second-SIGINT handler. The slow body (``begin_interrupt`` plus
  the early-escalation poll) is dispatched off the event loop via
  ``loop.run_in_executor`` with a done callback that logs any
  executor-body exception. This makes the cancel + handler-swap
  fast even when ``begin_interrupt`` would block.
* Second ``SIGINT`` force-kills tracked child processes via
  ``pm.list_active()`` (PGIDs) and exits with code 130.

The single source of truth for live processes is
``process_manager.list_active()``; the bridge does NOT maintain a
parallel pids set.

The interrupt dispatch is routed through :class:`InterruptDispatcher`
so the same wiring lives in both the sync ``handle_keyboard_interrupt``
path and this asyncio path. The ``controller`` parameter is
type-broadened to accept either an ``InterruptController`` or an
already-built ``InterruptDispatcher``; ``install_signal_handlers``
discriminates by ``isinstance`` inside the function body. The
parameter name is preserved for backward compatibility — the
broadening is type-only.

``install_signal_handlers`` returns an idempotent teardown callable
that removes the second-SIGINT handler installed by the first
handler. The teardown is safe to invoke twice.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
    run_shutdown_block,
)
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from ralph.interrupt.controller import InterruptController


@dataclass
class SignalBridge:
    """Bridge that routes OS signals to asyncio task cancellation and process cleanup.

    The bridge is intentionally minimal: a counter for the interrupt
    count and an optional connectivity-stop hook. The single source
    of truth for live processes is the :class:`ProcessManager`; the
    bridge never maintains its own PID set.
    """

    _interrupt_count: int = field(default=0, init=False)
    _connectivity_stop: Callable[[], None] | None = field(default=None, init=False)


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    root_task: asyncio.Task[object],
    bridge: SignalBridge,
    controller: InterruptController | InterruptDispatcher | None = None,
) -> Callable[[], None] | None:
    """Register SIGINT handlers that cancel ``root_task`` and forward to child PIDs.

    The fourth argument is type-broadened to accept an
    :class:`InterruptController` (legacy) OR an
    :class:`InterruptDispatcher` (new). Discrimination is by
    ``isinstance`` inside the body. When a controller is passed, the
    implementation synthesizes a dispatcher that forwards the
    controller's ``kill_process_group`` and ``hard_exit`` so the
    controller's injected exit callable is the one invoked on
    ``_second_sigint`` (PA-019).

    The returned callable is an idempotent teardown that removes the
    second-SIGINT handler installed by the first handler. Calling it
    twice is safe (a short-circuit flag is stored in the closure).
    """
    pm = get_process_manager()
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

    def _shutdown_block() -> None:
        run_shutdown_block(
            active_dispatcher,
            grace_period_s=pm.policy.default_grace_period_s,
            error_log_message="Interrupt shutdown block raised",
        )

    def _install_force_handlers() -> None:
        """Swap BOTH SIGINT and SIGTERM to the force-exit handler.

        AC-01 mixed-signal escalation: once a first interrupt has
        arrived (via either SIGINT or SIGTERM), ANY subsequent
        interrupt — regardless of which OS signal it carries — must
        trigger force-exit. Installing the force-exit handler on
        only the signal that arrived first would leave the other
        signal still pointing at the first-interrupt (graceful)
        handler, so a mixed second interrupt would start a fresh
        graceful shutdown instead of forcing exit.
        """
        try:
            loop.add_signal_handler(signal.SIGINT, _second_sigint)
        except Exception:
            logger.debug("add_signal_handler SIGINT failed during escalation")
        try:
            loop.add_signal_handler(signal.SIGTERM, _second_sigint)
        except Exception:
            logger.debug("add_signal_handler SIGTERM failed during escalation")

    def _first_sigint() -> None:
        bridge._interrupt_count += 1
        root_task.cancel()
        _install_force_handlers()
        future = loop.run_in_executor(None, _shutdown_block)
        future.add_done_callback(
            lambda f: (
                logger.warning("interrupt shutdown block failed: {}", f.exception())
                if not f.cancelled() and f.exception() is not None
                else None
            )
        )

    def _second_sigint() -> None:
        active = list(active_dispatcher.process_manager.list_active())
        active_dispatcher.force_exit(bridge_pgids=[r.pgid for r in active])

    def _first_sigterm() -> None:
        bridge._interrupt_count += 1
        root_task.cancel()
        _install_force_handlers()
        future = loop.run_in_executor(None, _shutdown_block)
        future.add_done_callback(
            lambda f: (
                logger.warning("interrupt shutdown block failed: {}", f.exception())
                if not f.cancelled() and f.exception() is not None
                else None
            )
        )

    loop.add_signal_handler(signal.SIGINT, _first_sigint)
    loop.add_signal_handler(signal.SIGTERM, _first_sigterm)

    teardown_state = {"done": False}

    def _teardown() -> None:
        if teardown_state["done"]:
            return
        teardown_state["done"] = True
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except Exception:
            logger.debug("remove_signal_handler raised during teardown")
        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except Exception:
            logger.debug("remove_signal_handler raised during teardown")

    return _teardown


__all__ = ["SignalBridge", "install_signal_handlers"]
