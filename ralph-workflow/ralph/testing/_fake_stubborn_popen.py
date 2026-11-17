from __future__ import annotations

from ralph.process.manager import DeadlineTimeoutExpired
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
        raise DeadlineTimeoutExpired(["fake-stubborn"], timeout or 0.0, timeout_cause="hard_cap")

    def terminate(self) -> None:
        return


__all__ = ["FakeStubbornPopen"]
