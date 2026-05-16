"""Test doubles for process-lifecycle tests.

Provides deterministic fake subprocess and psutil implementations that let tests
drive process state transitions without spawning real OS processes.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING


@dataclass
class SyncProcessOptions:
    """Options for synchronous subprocess creation."""

    cwd: str | None = None
    env: dict[str, str] | None = None
    stdin: int | None = None
    stdout: int | None = None
    stderr: int | None = None
    start_new_session: bool = False
    text: bool = False


@dataclass
class AsyncProcessOptions:
    """Options for asynchronous subprocess creation."""

    cwd: str | None = None
    env: dict[str, str] | None = None
    stdin: int | None = None
    stdout: int | None = None
    stderr: int | None = None
    start_new_session: bool = False


@dataclass
class ProcessState:
    """Process state flags."""

    returncode: int | None = None
    terminated: bool = False
    killed: bool = False


@dataclass
class ProcessStreams:
    """Process I/O streams."""

    stdin: IO[bytes] | None = None
    stdout: IO[bytes] | None = None
    stderr: IO[bytes] | None = None


@dataclass
class AsyncProcessStreams:
    """Async process I/O streams."""

    stdin: asyncio.StreamWriter | None = None
    stdout: asyncio.StreamReader | None = None
    stderr: asyncio.StreamReader | None = None

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import Protocol

    from ralph.process.manager import (
        _AsyncProcessLike,
        _PsutilProcessLike,
        _SyncProcessLike,
    )

    class _SyncFactoryCallable(Protocol):
        def __call__(
            self,
            command: Sequence[str],
            *,
            options: SyncProcessOptions,
        ) -> _SyncProcessLike: ...

    class _AsyncFactoryCallable(Protocol):
        async def __call__(
            self,
            command: Sequence[str],
            *,
            options: AsyncProcessOptions,
        ) -> _AsyncProcessLike: ...


@dataclass
class FakePsutilProcess:
    """Minimal psutil.Process-like fake for descendant_snapshot tests."""

    pid: int
    _running: bool = True
    _status: str = "sleeping"
    _create_time: float = 0.0
    _terminated: bool = False
    _killed: bool = False
    _children: list[FakePsutilProcess] = field(default_factory=list)
    stubborn: bool = False

    def is_running(self) -> bool:
        return (
            self._running
            and not self._terminated
            and not self._killed
            and self.status() != "zombie"
        )

    def status(self) -> str:
        if self._killed:
            return "zombie"
        if self._terminated:
            return "zombie"
        return self._status

    def create_time(self) -> float:
        return self._create_time

    def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
        return self._children

    def terminate(self) -> None:
        if not self.stubborn:
            self._terminated = True

    def kill(self) -> None:
        self._killed = True


class FakePsutil:
    """Fake psutil module for testing without real process operations.

    Simulates psutil.Process() and psutil.wait_procs() for deterministic
    testing of process tree operations.
    """

    NoSuchProcess = Exception
    AccessDenied = Exception

    def __init__(self) -> None:
        self._processes: dict[int, FakePsutilProcess] = {}
        self._next_pid = 1

    def Process(self, pid: int) -> FakePsutilProcess:
        """Mimics psutil.Process()."""
        if pid not in self._processes:
            self._processes[pid] = FakePsutilProcess(pid=pid)
        return self._processes[pid]

    def wait_procs(
        self,
        procs: Sequence[_PsutilProcessLike],
        timeout: float | None = None,
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        """Simulate wait_procs using the fake process lifecycle state."""
        dead: list[_PsutilProcessLike] = []
        alive: list[_PsutilProcessLike] = []
        for p in procs:
            if not p.is_running() or p.status() == "zombie":
                dead.append(p)
            else:
                alive.append(p)
        return dead, alive


class FakePopen:
    """Minimal subprocess.Popen-like fake for testing.

    Provides only the interface that ProcessManager/ManagedProcess uses.
    Tests control state transitions directly.
    """

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
        self.stdin = streams.stdin
        self.stdout = streams.stdout
        self.stderr = streams.stderr

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


class FakeTimeoutPopen:
    """FakePopen variant that raises TimeoutExpired on first communicate() with timeout.

    Simulates a slow process for testing timeout-handling code paths without
    spawning real subprocesses or sleeping.

    On the first communicate(timeout=T) call, raises subprocess.TimeoutExpired.
    On subsequent communicate() calls (after terminate()), returns partial_output.
    """

    def __init__(
        self,
        pid: int,
        *,
        partial_stdout: bytes = b"",
    ) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self._terminated = False
        self._killed = False
        self._partial_stdout = partial_stdout
        self._communicate_count = 0
        self.stdin: IO[bytes] | None = None
        self.stdout: IO[bytes] | None = None
        self.stderr: IO[bytes] | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode if self._returncode is not None else 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self._communicate_count += 1
        if self._communicate_count == 1 and timeout is not None:
            raise subprocess.TimeoutExpired(
                cmd="fake-process",
                timeout=timeout,
                output=self._partial_stdout,
                stderr=b"",
            )
        return self._partial_stdout, b""

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True


class FakeControllableAsyncProcess:
    """Async process fake with controllable stdout and completion.

    Suitable for testing code paths that need a process to stay running while
    assertions are made, then complete deterministically — without spawning real
    subprocesses or using real wall-clock sleeps.

    Usage::

        completion = asyncio.Event()
        proc = FakeControllableAsyncProcess(
            pid=1,
            stdout_data=b"ready\n",
            completion_event=completion,
        )
        # ... start task that reads proc.stdout and waits on proc.wait() ...
        # when you're done: completion.set() to let it finish
    """

    def __init__(
        self,
        pid: int,
        *,
        stdout_data: bytes = b"",
        returncode: int = 0,
        completion_event: asyncio.Event | None = None,
    ) -> None:
        self.pid = pid
        self._final_returncode = returncode
        self._returncode: int | None = None
        self._completion_event = completion_event or asyncio.Event()
        self._stdout: asyncio.StreamReader = asyncio.StreamReader()
        if stdout_data:
            self._stdout.feed_data(stdout_data)
            self._stdout.feed_eof()
        else:
            self._stdout.feed_eof()
        self.stdin = None
        self.stderr = None

    @property
    def stdout(self) -> asyncio.StreamReader:
        return self._stdout

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        await self._completion_event.wait()
        self._returncode = self._final_returncode
        return self._final_returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        await self._completion_event.wait()
        return b"", b""

    def terminate(self) -> None:
        self._returncode = self._final_returncode
        self._completion_event.set()

    def kill(self) -> None:
        self._returncode = self._final_returncode
        self._completion_event.set()


def make_sync_process_factory(
    pids: Iterator[int],
    *,
    returncode: int | None = None,
    terminated: bool = False,
    killed: bool = False,
) -> _SyncFactoryCallable:
    """Create a sync process factory that generates FakePopen with sequential PIDs.

    Args:
        pids: Iterator that returns the next PID (e.g., itertools.count(1))
        returncode: Set returncode on all created processes
        terminated: Set terminated flag on all created processes
        killed: Set killed flag on all created processes
    """

    def factory(
        command: Sequence[str],
        *,
        options: SyncProcessOptions,
    ) -> FakePopen:
        return FakePopen(
            pid=next(pids),
            state=ProcessState(
                returncode=returncode,
                terminated=terminated,
                killed=killed,
            ),
        )

    return factory


def make_async_process_factory(
    pids: Iterator[int],
    *,
    returncode: int | None = None,
) -> _AsyncFactoryCallable:
    """Create an async process factory that generates FakeAsyncProcess with sequential PIDs."""

    async def factory(
        command: Sequence[str],
        *,
        options: AsyncProcessOptions,
    ) -> FakeAsyncProcess:
        return FakeAsyncProcess(
            pid=next(pids),
            state=ProcessState(returncode=returncode),
        )

    return factory


def make_psutil_factory(
    processes: dict[int, FakePsutilProcess],
) -> FakePsutil:
    """Create a FakePsutil instance pre-populated with specific processes.

    Args:
        processes: Dict mapping PIDs to FakePsutilProcess instances
    """
    fake = FakePsutil()
    fake._processes = dict(processes)
    return fake


__all__ = [
    "AsyncProcessOptions",
    "AsyncProcessStreams",
    "FakeAsyncProcess",
    "FakeControllableAsyncProcess",
    "FakePopen",
    "FakePsutil",
    "FakePsutilProcess",
    "FakeTimeoutPopen",
    "ProcessState",
    "ProcessStreams",
    "SyncProcessOptions",
    "make_async_process_factory",
    "make_psutil_factory",
    "make_sync_process_factory",
]
