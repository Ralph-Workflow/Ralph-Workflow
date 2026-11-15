from __future__ import annotations

import subprocess

from ralph.testing._fake_popen_base import FakePopen


class FakeStubbornPopen(FakePopen):
    __slots__ = ()

    def __init__(self, pid: int, *, final_returncode: int = -9) -> None:
        super().__init__(pid)
        self._final_returncode = final_returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._killed:
            self._returncode = self._final_returncode
            return self._final_returncode
        raise subprocess.TimeoutExpired(cmd="fake-stubborn", timeout=timeout or 0.0)

    def terminate(self) -> None:
        return
