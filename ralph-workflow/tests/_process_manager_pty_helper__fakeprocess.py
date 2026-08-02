from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _FakePtyProcess:
    pid: int
    master_fd: int
    slave_fd: int
    returncode: int | None = None
    terminated: bool = False
    killed: bool = False
    closed: bool = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        return self.master_fd

    def isatty(self) -> bool:
        return True
