from __future__ import annotations

from typing import IO

from ralph.process.manager import DeadlineTimeoutExpired
from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams


class FakePopen:
    __slots__ = (
        "__dict__",
        "_final_returncode",
        "_killed",
        "_returncode",
        "_terminated",
        "pid",
        "stderr",
        "stdin",
        "stdout",
    )

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


class FakeImmortalPopen(FakePopen):
    __slots__ = ()

    def wait(self, timeout: float | None = None) -> int:
        raise DeadlineTimeoutExpired(["fake-immortal"], timeout or 0.0, timeout_cause="hard_cap")

    def terminate(self) -> None:
        return

    def kill(self) -> None:
        return


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


__all__ = ["FakeImmortalPopen", "FakePopen", "FakeStubbornPopen"]
