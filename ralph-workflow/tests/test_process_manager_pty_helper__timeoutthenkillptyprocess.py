from __future__ import annotations

from tests.test_process_manager_pty_helper__fakeprocess import _FakePtyProcess


class _TimeoutThenKillPtyProcess(_FakePtyProcess):
    wait_calls: int = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        if self.killed:
            return self.returncode if self.returncode is not None else -9
        raise TimeoutError
