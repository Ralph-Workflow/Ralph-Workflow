"""Regression: stdio transport close() bounds wait() and kills on timeout.

The non-ManagedProcess close path did `proc.terminate(); proc.wait()` with no
timeout, so a process that ignored SIGTERM could block the close (and the MCP
server) forever. close() must bound the wait and escalate to kill().
"""

from __future__ import annotations

import subprocess
from io import BytesIO
from typing import IO

import pytest

from ralph.mcp.protocol.transport import StdioTransport


class _SlowToDieProcess:
    def __init__(self) -> None:
        self.stdin: IO[bytes] | None = BytesIO()
        self.stdout: IO[bytes] | None = BytesIO()
        self.stderr: IO[bytes] | None = BytesIO()
        self.killed = False
        self.wait_calls = 0

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired(cmd="proc", timeout=timeout or 0.0)
        return 0

    def kill(self) -> None:
        self.killed = True


class _NoopThread:
    def start(self) -> None:
        return None


@pytest.mark.asyncio
async def test_close_bounds_wait_and_kills_when_terminate_hangs() -> None:
    proc = _SlowToDieProcess()
    transport = StdioTransport(
        ["proc"],
        process_factory=lambda command, cwd: proc,
        thread_factory=lambda target, daemon: _NoopThread(),
    )
    transport.start()

    await transport.close()

    assert proc.killed, "close() must kill the process when the bounded wait() times out"
    assert proc.wait_calls >= 2, "close() must re-wait after kill()"
