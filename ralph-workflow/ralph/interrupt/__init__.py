"""Interrupt-handling helpers for unattended runs.

The orchestrator uses this module to install a process-wide SIGINT handler and
record whether a user interruption has been requested. The state is intentionally
simple so both CLI code and long-running loops can check it safely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.interrupt.asyncio_bridge import install_signal_handlers
from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE,
    InterruptController,
    controller_from_process_manager,
    install_force_kill_handler,
)
from ralph.interrupt.dispatcher import (
    INTERRUPT_HARD_KILL_BUDGET_SECONDS,
    SIGINT_PROGRESS_POLL_INTERVAL_SECONDS,
    InterruptDispatcher,
)
from ralph.interrupt.dispatcher import dispatcher_from_process_manager as build_dispatcher
from ralph.interrupt.state import request_user_interrupt, user_interrupted_occurred

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ProcessManager


def dispatcher_from_process_manager(
    *,
    process_manager: ProcessManager | None = None,
    stop_connectivity: Callable[[], None] | None = None,
    record_interrupt: Callable[[], None] | None = None,
    kill_process_group: Callable[[int, int], None] | None = None,
    hard_exit: Callable[[int], None] | None = None,
    poll_interval_s: float = SIGINT_PROGRESS_POLL_INTERVAL_SECONDS,
    hard_kill_budget_s: float = INTERRUPT_HARD_KILL_BUDGET_SECONDS,
    kill_label: str = "invoke:",
) -> InterruptDispatcher:
    """Convenience re-export of :func:`ralph.interrupt.dispatcher.dispatcher_from_process_manager`.

    Forwards all kwargs verbatim. Defined at this layer so callers can
    ``from ralph.interrupt import dispatcher_from_process_manager``
    without depending on the dispatcher module path directly.
    """
    return build_dispatcher(
        process_manager=process_manager,
        stop_connectivity=stop_connectivity,
        record_interrupt=record_interrupt,
        kill_process_group=kill_process_group,
        hard_exit=hard_exit,
        poll_interval_s=poll_interval_s,
        hard_kill_budget_s=hard_kill_budget_s,
        kill_label=kill_label,
    )


__all__ = [
    "INTERRUPT_EXIT_CODE",
    "INTERRUPT_HARD_KILL_BUDGET_SECONDS",
    "SIGINT_PROGRESS_POLL_INTERVAL_SECONDS",
    "InterruptController",
    "InterruptDispatcher",
    "controller_from_process_manager",
    "dispatcher_from_process_manager",
    "install_force_kill_handler",
    "install_signal_handlers",
    "request_user_interrupt",
    "user_interrupted_occurred",
]
