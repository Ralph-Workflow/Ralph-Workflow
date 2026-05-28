from __future__ import annotations

import subprocess
from typing import IO

from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams


class FakePopen:
    """Minimal subprocess.Popen-like fake for testing."""

    def __init__(
        self,
        pid: int,
        *,
        state: ProcessState | None = None,
        streams: ProcessStreams | None = None,
    ) -> None:
        self.pid = pid
        state = state or ProcessState()
        streams = streams or ProcessStreams()
        self._returncode = state.returncode
        self._terminated = state.terminated
        self._killed = state.killed
        self.stdin: IO[bytes] | None = streams.stdin
        self.stdout: IO[bytes] | None = streams.stdout
        self.stderr: IO[bytes] | None = streams.stderr

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode if self._returncode is not None else 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True


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