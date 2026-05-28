from __future__ import annotations

import subprocess
from typing import IO


class FakeImmortalPopen:
    """FakePopen that never terminates regardless of signal.

    wait(timeout=...) always raises subprocess.TimeoutExpired.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.stdin: IO[bytes] | None = None
        self.stdout: IO[bytes] | None = None
        self.stderr: IO[bytes] | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(cmd="fake-immortal", timeout=timeout or 0.0)

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass
