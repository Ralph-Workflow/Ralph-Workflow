"""Pytest plugin enforcing Ralph's hard suite wall-clock timeout.

This plugin complements the per-test timeout in ``tests/conftest.py`` by
starting a session-wide watchdog in the controller process. Unlike cooperative
session timeout plugins, the watchdog terminates descendant worker processes and
exits the pytest process once the suite deadline is exceeded.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import psutil

from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    SUITE_TIMEOUT_ENV,
    SuiteTimeoutError,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    import pytest


_WATCHDOG_ATTR = "_ralph_suite_watchdog"
_TIMEOUT_EXIT_CODE = 124


@dataclass
class _SuiteWatchdog:
    cancel_event: threading.Event
    thread: threading.Thread


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _emit_timeout_message(timeout_seconds: float) -> None:
    message = str(SuiteTimeoutError(timeout_seconds))
    sys.stdout.flush()
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def _terminate_descendants() -> None:
    try:
        current_process = psutil.Process(os.getpid())
        descendants = current_process.children(recursive=True)
    except psutil.Error:
        return

    for descendant in descendants:
        try:
            descendant.terminate()
        except psutil.Error:
            continue

    _gone, alive = psutil.wait_procs(descendants, timeout=0.2)
    for descendant in alive:
        try:
            descendant.kill()
        except psutil.Error:
            continue
    psutil.wait_procs(alive, timeout=0.2)


def _watchdog_main(timeout_seconds: float, cancel_event: threading.Event) -> None:
    if cancel_event.wait(timeout_seconds):
        return
    _emit_timeout_message(timeout_seconds)
    _terminate_descendants()
    os._exit(_TIMEOUT_EXIT_CODE)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Start the controller-only watchdog that enforces the suite wall-clock cap."""
    config = session.config
    if _is_xdist_worker(config):
        return

    timeout_seconds = timeout_seconds_from_env(SUITE_TIMEOUT_ENV, DEFAULT_SUITE_TIMEOUT_SECONDS)
    if timeout_seconds <= 0:
        return

    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_watchdog_main,
        args=(timeout_seconds, cancel_event),
        name="ralph-pytest-suite-watchdog",
        daemon=True,
    )
    setattr(config, _WATCHDOG_ATTR, _SuiteWatchdog(cancel_event=cancel_event, thread=thread))
    thread.start()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Cancel the suite watchdog when pytest finishes normally."""
    del exitstatus
    watchdog = cast("_SuiteWatchdog | None", getattr(session.config, _WATCHDOG_ATTR, None))
    if watchdog is None:
        return
    watchdog.cancel_event.set()
    watchdog.thread.join(timeout=0.1)
