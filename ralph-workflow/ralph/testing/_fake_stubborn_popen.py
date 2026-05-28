from __future__ import annotations

import subprocess
from typing import IO


class FakeStubbornPopen:
    """FakePopen that ignores SIGTERM but obeys SIGKILL.

    wait(timeout=...) raises subprocess.TimeoutExpired until kill() is called.
    After kill(), wait() returns final_returncode.
    """

    def __init__(self, pid: int, *, final_returncode: int = -9) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self._final_returncode = final_returncode
        self._killed = False
        self.stdin: IO[bytes] | None = None
        self.stdout: IO[bytes] | None = None
        self.stderr: IO[bytes] | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._killed:
            self._returncode = self._final_returncode
            return self._final_returncode
        raise subprocess.TimeoutExpired(cmd="fake-stubborn", timeout=timeout or 0.0)

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self._killed = True
