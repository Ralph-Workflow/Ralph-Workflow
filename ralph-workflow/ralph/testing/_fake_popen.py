from __future__ import annotations

from typing import IO, TYPE_CHECKING

from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams

if TYPE_CHECKING:
    pass


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
