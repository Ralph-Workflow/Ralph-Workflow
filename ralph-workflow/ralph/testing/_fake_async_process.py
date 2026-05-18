from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing._async_process_streams import AsyncProcessStreams
from ralph.testing._process_state import ProcessState

if TYPE_CHECKING:
    import asyncio


class FakeAsyncProcess:
    """Minimal asyncio.subprocess.Process-like fake for testing."""

    _stdin: asyncio.StreamWriter | None
    _stdout: asyncio.StreamReader | None
    _stderr: asyncio.StreamReader | None

    def __init__(
        self,
        pid: int,
        *,
        state: ProcessState | None = None,
        streams: AsyncProcessStreams | None = None,
    ) -> None:
        self.pid = pid
        state = state or ProcessState()
        streams = streams or AsyncProcessStreams()
        self._returncode = state.returncode
        self._terminated = state.terminated
        self._killed = state.killed
        self._stdin = streams.stdin
        self._stdout = streams.stdout
        self._stderr = streams.stderr

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        return self._stdin

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self._stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return self._stderr

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        return self._returncode if self._returncode is not None else 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return b"", b""

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True
