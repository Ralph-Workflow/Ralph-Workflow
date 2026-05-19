"""Keyboard interrupt handling for the pipeline runner."""

from __future__ import annotations

import os
import signal
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

from loguru import logger

from ralph.interrupt import controller_from_process_manager
from ralph.interrupt.controller import install_force_kill_handler
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable


def handle_keyboard_interrupt(monitor_stop: Callable[[], None] | None = None) -> None:
    """Gracefully stop tracked children, escalating on a second SIGINT."""
    process_manager = get_process_manager()
    controller = controller_from_process_manager(
        process_manager=process_manager,
        stop_connectivity=monitor_stop,
    )
    interrupt_done = threading.Event()
    interrupt_error: list[BaseException] = []

    def _force_exit() -> None:
        kill_method = os.killpg if hasattr(os, "killpg") else os.kill
        try:
            active_records = list(process_manager.list_active())
        except Exception:
            active_records = []
        for record in active_records:
            with suppress(ProcessLookupError, PermissionError):
                if kill_method is os.killpg:
                    kill_method(record.pgid, signal.SIGKILL)
                else:
                    kill_method(record.pid, signal.SIGKILL)
        os._exit(130)

    def _begin_interrupt() -> None:
        try:
            controller.begin_interrupt(grace_period_s=process_manager.policy.default_grace_period_s)
        except BaseException as exc:
            interrupt_error.append(exc)
        finally:
            interrupt_done.set()

    restore_force_kill = install_force_kill_handler(_force_exit)
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
