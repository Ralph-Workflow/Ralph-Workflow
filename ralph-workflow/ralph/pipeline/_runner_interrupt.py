
from __future__ import annotations

import signal
import threading
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.interrupt.controller import install_force_kill_handler
from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
    run_shutdown_block,
)
from ralph.process.manager import ProcessManager, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter


_DEFAULT_SIGNAL_GETTER = cast(
    "SignalGetter", signal.getsignal
)
_DEFAULT_SIGNAL_SETTER = cast(
    "SignalSetter", signal.signal
)


def handle_keyboard_interrupt(
    monitor_stop: Callable[[], None] | None = None,
    *,
    dispatcher: InterruptDispatcher | None = None,
    process_manager: ProcessManager | None = None,
    poll_interval_s: float = 0.05,
    signal_getter: SignalGetter | None = None,
    signal_setter: SignalSetter | None = None,
) -> None:
    if dispatcher is not None and monitor_stop is not None:
        raise RuntimeError(
            "handle_keyboard_interrupt: monitor_stop is ignored when dispatcher "
            "is pre-built; pass monitor_stop only when dispatcher is None. "
            "See ralph/pipeline/_runner_interrupt.py."
        )
    resolved_pm: ProcessManager = (
        process_manager if process_manager is not None else get_process_manager()
    )
    if dispatcher is None:
        dispatcher = dispatcher_from_process_manager(
            process_manager=resolved_pm,
            stop_connectivity=monitor_stop,
        )
    if isinstance(resolved_pm, ProcessManager):
        for record in resolved_pm.list_active():
            resolved_pm.record_terminal_reason(record.pid, "operator_cancellation")
    resolved_getter: SignalGetter = signal_getter or _DEFAULT_SIGNAL_GETTER
    resolved_setter: SignalSetter = signal_setter or _DEFAULT_SIGNAL_SETTER
    interrupt_done = threading.Event()
    interrupt_error: list[BaseException] = []

    def _force_exit() -> None:
        active_records = list(resolved_pm.list_active())
        bridge_pgids = [r.pgid for r in active_records]
        dispatcher.force_exit(bridge_pgids=bridge_pgids)

    def _begin_interrupt() -> None:
        try:
            run_shutdown_block(
                dispatcher,
                grace_period_s=resolved_pm.policy.default_grace_period_s,
                error_log_message="Interrupt controller raised during KeyboardInterrupt",
            )
        except Exception as exc:
            interrupt_error.append(exc)
        finally:
            interrupt_done.set()

    restore_force_kill = install_force_kill_handler(
        _force_exit,
        signal_getter=resolved_getter,
        signal_setter=resolved_setter,
    )
    restore_force_kill_term = install_force_kill_handler(
        _force_exit,
        signal_getter=resolved_getter,
        signal_setter=resolved_setter,
        signum=signal.SIGTERM,
    )
    interrupt_thread = threading.Thread(target=_begin_interrupt, daemon=True)
    interrupt_thread.start()
    try:
        while not interrupt_done.wait(timeout=poll_interval_s):
            continue
    finally:
        with suppress(Exception):
            restore_force_kill()
        with suppress(Exception):
            restore_force_kill_term()
    if interrupt_error:
        logger.warning("Interrupt controller raised during KeyboardInterrupt")
