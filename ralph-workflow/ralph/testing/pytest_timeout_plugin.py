from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    SUITE_TIMEOUT_ENV,
    SuiteTimeoutError,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    import pytest



_TIMEOUT_EXIT_CODE = 124


@dataclass
class _SuiteWatchdog:
    cancel_event: threading.Event
    thread: threading.Thread


_ACTIVE_WATCHDOG: list[_SuiteWatchdog] = []


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
    except (psutil.Error, OSError):
        return

    for descendant in descendants:
        try:
            descendant.terminate()
        except (psutil.Error, OSError):
            continue

    _gone, alive = psutil.wait_procs(descendants, timeout=0.2)
    for descendant in alive:
        try:
            descendant.kill()
        except (psutil.Error, OSError):
            continue
    psutil.wait_procs(alive, timeout=0.2)


def _watchdog_main(timeout_seconds: float, cancel_event: threading.Event) -> None:
    if cancel_event.wait(timeout_seconds):
        return
    _emit_timeout_message(timeout_seconds)
    _terminate_descendants()
    os._exit(_TIMEOUT_EXIT_CODE)


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    if _is_xdist_worker(config):
        return

    timeout_seconds = timeout_seconds_from_env(SUITE_TIMEOUT_ENV, DEFAULT_SUITE_TIMEOUT_SECONDS)

    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_watchdog_main,
        args=(timeout_seconds, cancel_event),
        name="ralph-pytest-suite-watchdog",
        daemon=True,
    )
    _ACTIVE_WATCHDOG.append(_SuiteWatchdog(cancel_event=cancel_event, thread=thread))
    thread.start()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    watchdog = _ACTIVE_WATCHDOG.pop() if _ACTIVE_WATCHDOG else None
    if watchdog is None:
        return
    watchdog.cancel_event.set()
    watchdog.thread.join(timeout=0.1)
