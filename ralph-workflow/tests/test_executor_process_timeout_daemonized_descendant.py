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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_SCRIPT = "echo partial-output; (/bin/sleep 30 & echo $! ) ; exec /bin/sleep 100"
_TIMEOUT_S = 0.5
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
        time.sleep(0.03)
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
