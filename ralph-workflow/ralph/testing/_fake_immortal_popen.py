from __future__ import annotations

import subprocess

from ralph.testing._fake_popen_base import FakePopen


class FakeImmortalPopen(FakePopen):
    __slots__ = ()

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(cmd="fake-immortal", timeout=timeout or 0.0)

    def terminate(self) -> None:
        return

    def kill(self) -> None:
        return
