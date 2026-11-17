from __future__ import annotations

from ralph.process.manager import DeadlineTimeoutExpired
from ralph.testing._fake_popen_base import FakePopen


class FakeImmortalPopen(FakePopen):
    __slots__ = ()

    def wait(self, timeout: float | None = None) -> int:
        raise DeadlineTimeoutExpired(["fake-immortal"], timeout or 0.0, timeout_cause="hard_cap")

    def terminate(self) -> None:
        return

    def kill(self) -> None:
        return


__all__ = ["FakeImmortalPopen"]
