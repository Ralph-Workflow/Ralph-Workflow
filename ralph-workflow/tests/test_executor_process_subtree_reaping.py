from __future__ import annotations

import os
import signal
import sys
import textwrap
import time

import pytest

from ralph.executor._process_run_options import ProcessRunOptions
from ralph.executor.process import TIMEOUT_EXIT_CODE, run_process

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]

_ORPHAN_MAKER = textwrap.dedent(
    """
    import os, sys, time
    pid = os.fork()
    if pid == 0:
        # Grandchild: outlive the exec call. It stays in the child's process
        # group, exactly like a forked Minitest/parallel worker — it does NOT
        # setsid() away, which no real worker does either.
        # Release the inherited stdout/stderr pipes first, or communicate()
        # blocks on them until this process exits and the call never returns.
        for fd in (0, 1, 2):
            try:
                os.close(fd)
            except OSError:
                pass
        time.sleep(30)
        sys.exit(0)
    sys.stdout.write(str(pid) + "\\n")
    sys.stdout.flush()
    """
).strip()

_ORPHAN_MAKER_HANGS = _ORPHAN_MAKER + "\ntime.sleep(30)\n"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid: int, timeout: float = 0.4) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.005)
    return not _alive(pid)


def _reap_if_survived(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.005)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-tree semantics")
def test_completed_exec_leaves_no_orphaned_grandchild() -> None:
    result = run_process(
        sys.executable,
        ["-c", _ORPHAN_MAKER],
        options=ProcessRunOptions(timeout=5.0),
    )

    assert result.returncode == 0
    grandchild = int(result.stdout.strip())
    try:
        assert _wait_gone(grandchild), (
            f"exec returned but grandchild {grandchild} is still alive — "
            "descendants are orphaning to PID 1 again"
        )
    finally:
        _reap_if_survived(grandchild)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-tree semantics")
def test_timed_out_exec_leaves_no_orphaned_grandchild() -> None:
    result = run_process(
        sys.executable,
        ["-c", _ORPHAN_MAKER_HANGS],
        options=ProcessRunOptions(timeout=0.15),
    )

    assert result.returncode == TIMEOUT_EXIT_CODE
    grandchild = int(result.stdout.strip())
    try:
        assert _wait_gone(grandchild), (
            f"exec timed out but grandchild {grandchild} is still alive — "
            "the timeout path is reaping only the direct child"
        )
    finally:
        _reap_if_survived(grandchild)
