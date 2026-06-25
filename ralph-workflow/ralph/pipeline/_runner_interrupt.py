"""Keyboard interrupt handling for the pipeline runner."""

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
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter
    from ralph.process.manager import ProcessManager


_DEFAULT_SIGNAL_GETTER = cast("SignalGetter", signal.getsignal)
_DEFAULT_SIGNAL_SETTER = cast("SignalSetter", signal.signal)


def handle_keyboard_interrupt(
    monitor_stop: Callable[[], None] | None = None,
    *,
    dispatcher: InterruptDispatcher | None = None,
    process_manager: ProcessManager | None = None,
    poll_interval_s: float = 0.05,
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

    The optional ``process_manager`` and ``poll_interval_s`` kwargs
    close the entry-point seam a real Ctrl+C reaches inside the
    pipeline loop. ``process_manager`` replaces the
    ``get_process_manager()`` singleton (production callers omit it
    and the singleton is used; tests inject a fake). ``poll_interval_s``
    replaces the literal ``0.05`` busy-wait timeout; the default is
    unchanged from production behavior. See ADR-0001 D5.
    """
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
            # Exception not BaseException: KeyboardInterrupt and SystemExit
            # must propagate. See AGENTS.md and ADR-0001 D6.
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
