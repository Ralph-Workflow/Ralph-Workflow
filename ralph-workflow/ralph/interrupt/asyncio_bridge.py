
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

    _interrupt_count: int = field(default=0, init=False)
    _connectivity_stop: Callable[[], None] | None = field(default=None, init=False)


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    root_task: asyncio.Task[object],
    bridge: SignalBridge,
    controller: InterruptController | InterruptDispatcher | None = None,
) -> Callable[[], None] | None:
    pm = get_process_manager()
    if controller is None:
        active_dispatcher: InterruptDispatcher = dispatcher_from_process_manager(
            process_manager=pm,
            stop_connectivity=bridge._connectivity_stop,
        )
    elif isinstance(controller, InterruptDispatcher):
        active_dispatcher = controller
    else:
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
