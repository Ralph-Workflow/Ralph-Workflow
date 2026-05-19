from __future__ import annotations

import subprocess
from typing import IO


class FakeTimeoutPopen:
    """FakePopen variant that raises TimeoutExpired on first communicate() with timeout."""

    def __init__(
        self,
        pid: int,
        *,
        partial_stdout: bytes = b"",
    ) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self._terminated = False
        self._killed = False
        self._partial_stdout = partial_stdout
        self._communicate_count = 0
        self.stdin: IO[bytes] | None = None
        self.stdout: IO[bytes] | None = None
        self.stderr: IO[bytes] | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode if self._returncode is not None else 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self._communicate_count += 1
        if self._communicate_count == 1 and timeout is not None:
            raise subprocess.TimeoutExpired(
                cmd="fake-process",
                timeout=timeout,
                output=self._partial_stdout,
                stderr=b"",
            )
        return self._partial_stdout, b""

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True
