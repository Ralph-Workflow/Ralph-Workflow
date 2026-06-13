"""Keyboard interrupt handling for the pipeline runner."""

from __future__ import annotations

import signal
import threading
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.interrupt.controller import install_force_kill_handler
from ralph.interrupt.dispatcher import (
    INTERRUPT_HARD_KILL_BUDGET_SECONDS,
    InterruptDispatcher,
    dispatcher_from_process_manager,
)
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter


_DEFAULT_SIGNAL_GETTER = cast("SignalGetter", signal.getsignal)
_DEFAULT_SIGNAL_SETTER = cast("SignalSetter", signal.signal)


def handle_keyboard_interrupt(
    monitor_stop: Callable[[], None] | None = None,
    *,
    dispatcher: InterruptDispatcher | None = None,
    signal_getter: SignalGetter | None = None,
    signal_setter: SignalSetter | None = None,
) -> None:
    """Gracefully stop tracked children, escalating on a second SIGINT.

    The first SIGINT routes through the dispatcher's ``begin_interrupt``
    (which injects ``kill_label='invoke:'`` and routes through the
    label-targeted shutdown). A poll thread monitors the agent's
    process group for no-progress and escalates to SIGKILL before
    ``INTERRUPT_HARD_KILL_BUDGET_SECONDS`` elapses.

    The second SIGINT invokes ``dispatcher.force_exit(...)`` against
    the records still tracked by the process manager. The
    ``dispatcher`` is idempotent on ``force_exit``, so a third or
    fourth SIGINT is a no-op.

    The optional ``signal_getter``/``signal_setter`` kwargs are the
    test seam: production callers omit them and the module defaults
    (``signal.getsignal``/``signal.signal``) are used; tests pass
    fakes to assert handler install/restore.
    """
    process_manager = get_process_manager()
    if dispatcher is None:
        dispatcher = dispatcher_from_process_manager(
            process_manager=process_manager,
            stop_connectivity=monitor_stop,
        )
    resolved_getter: SignalGetter = signal_getter or _DEFAULT_SIGNAL_GETTER
    resolved_setter: SignalSetter = signal_setter or _DEFAULT_SIGNAL_SETTER
    interrupt_done = threading.Event()
    interrupt_error: list[BaseException] = []

    def _force_exit() -> None:
        active_records = list(process_manager.list_active())
        bridge_pgids = [r.pgid for r in active_records]
        dispatcher.force_exit(bridge_pgids=bridge_pgids)

    def _begin_interrupt() -> None:
        try:
            dispatcher.begin_interrupt(
                grace_period_s=process_manager.policy.default_grace_period_s,
            )
            poll_thread = threading.Thread(
                target=dispatcher.run_early_escalation_poll,
                args=(INTERRUPT_HARD_KILL_BUDGET_SECONDS,),
                daemon=True,
            )
            poll_thread.start()
            poll_thread.join(timeout=INTERRUPT_HARD_KILL_BUDGET_SECONDS + 0.1)
        except BaseException as exc:
            interrupt_error.append(exc)
        finally:
            interrupt_done.set()

    restore_force_kill = install_force_kill_handler(
        _force_exit,
        signal_getter=resolved_getter,
        signal_setter=resolved_setter,
    )
    interrupt_thread = threading.Thread(target=_begin_interrupt, daemon=True)
    interrupt_thread.start()
    try:
        while not interrupt_done.wait(timeout=0.05):
            continue
    finally:
        with suppress(Exception):
            restore_force_kill()
    if interrupt_error:
        logger.warning("Interrupt controller raised during KeyboardInterrupt")
