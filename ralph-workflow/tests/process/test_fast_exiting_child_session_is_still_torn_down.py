from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from typing import TYPE_CHECKING

import psutil
import pytest

from ralph.agents.timeout_clock import SystemClock
from ralph.process.manager import ProcessManager, SpawnOptions, default_sync_process_factory
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.process.teardown import DefaultProcessTeardown

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = [pytest.mark.timeout_seconds(5)]

_CHILD_LEAVING_A_GRANDCHILD = (
    "import os, sys;"
    "pid = os.fork();"
    "(open({pid_file}, 'w').write(str(pid)), sys.exit(0)) if pid else "
    "os.execv(sys.executable, [sys.executable, '-c', 'import time; time.sleep(30)']);"
)


def _wait_until(predicate: object, *, seconds: float) -> bool:
    clock = SystemClock()
    deadline = clock.monotonic() + seconds
    pause = threading.Event()
    while clock.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        clock.wait_for_event(pause, 0.01)
    return False


def _is_zombie_or_gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _returned_after_the_child_exits(command: Sequence[str], opts: SpawnOptions) -> object:
    proc = default_sync_process_factory(command, opts)
    assert _wait_until(lambda: _is_zombie_or_gone(proc.pid), seconds=3.0)
    return proc


def _stopped(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_a_child_that_exits_before_registration_still_has_its_session_torn_down(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    manager = ProcessManager(sync_process_factory=_returned_after_the_child_exits)
    child = manager.spawn(
        [sys.executable, "-c", _CHILD_LEAVING_A_GRANDCHILD.format(pid_file=repr(str(pid_file)))],
        SpawnOptions(label="invoke:fast-exit"),
    )
    assert _wait_until(pid_file.exists, seconds=3.0)
    grandchild_pid = int(pid_file.read_text().strip())
    try:
        DefaultProcessTeardown().teardown_subtree(
            child.pid, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN, owner_label="invoke"
        )
        assert _wait_until(lambda: _stopped(grandchild_pid), seconds=3.0)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(grandchild_pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            child.wait(timeout=1.0)
