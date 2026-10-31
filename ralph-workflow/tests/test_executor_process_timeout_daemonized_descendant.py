"""Regression: an exec timeout must hold when a descendant keeps the pipes open.

Observed 2026-10-30. On timeout, ``run_process`` / ``run_process_async`` called
``handle.terminate()``, which stops only the psutil-enumerated tree. A
daemonized descendant (``(sleep N &)``: its parent subshell exits, so it is no
longer anyone's child) is invisible to that walk but still holds the
stdout/stderr pipes. The group sweep that reaches it ran only in ``finally``,
AFTER the post-terminate drain, so:

- ``run_process_async`` awaited the drain without any bound and never
  returned while the descendant lived -- ``timeout=`` could not be met;
- ``run_process`` waited an extra 5 s drain window and then replaced the
  output it had already captured with ``b""``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from ralph.executor.process import (
    TIMEOUT_EXIT_CODE,
    ProcessRunOptions,
    run_process,
    run_process_async,
)

# Real POSIX process groups and pipes: the defect is a pipe held open by a
# re-parented group member, which no fake process can stand in for.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

# The daemonized sleeper prints its PID so the test can prove it was reaped.
_SCRIPT = "echo partial-output; (/bin/sleep 30 & echo $! ) ; exec /bin/sleep 100"
_TIMEOUT_S = 0.5
# Generous headroom over the timeout, but well under the old 5 s drain.
_RETURN_WITHIN_S = 2.5


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _assert_sleeper_reaped(stdout: str) -> None:
    lines = stdout.split()
    sleeper = int(lines[-1])
    deadline = time.monotonic() + 1.0
    while _alive(sleeper) and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert not _alive(sleeper), f"daemonized descendant {sleeper} outlived the exec call"
    finally:
        if _alive(sleeper):
            os.kill(sleeper, 9)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_sync_timeout_returns_promptly_with_partial_output() -> None:
    start = time.monotonic()
    result = run_process("/bin/sh", ["-c", _SCRIPT], options=ProcessRunOptions(timeout=_TIMEOUT_S))
    elapsed = time.monotonic() - start

    assert result.returncode == TIMEOUT_EXIT_CODE
    assert elapsed < _RETURN_WITHIN_S, f"timeout={_TIMEOUT_S}s but returned after {elapsed:.2f}s"
    assert result.stdout.startswith("partial-output\n")
    _assert_sleeper_reaped(result.stdout)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_async_timeout_returns_promptly_with_partial_output() -> None:
    async def _run() -> tuple[float, int, str]:
        start = time.monotonic()
        result = await asyncio.wait_for(
            run_process_async("/bin/sh", ["-c", _SCRIPT], timeout=_TIMEOUT_S),
            timeout=6.0,
        )
        return time.monotonic() - start, result.returncode, result.stdout

    elapsed, returncode, stdout = asyncio.run(_run())

    assert returncode == TIMEOUT_EXIT_CODE
    assert elapsed < _RETURN_WITHIN_S, f"timeout={_TIMEOUT_S}s but returned after {elapsed:.2f}s"
    assert stdout.startswith("partial-output\n")
    _assert_sleeper_reaped(stdout)
