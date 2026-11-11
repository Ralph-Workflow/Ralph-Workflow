from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ralph.testing._async_process_streams import AsyncProcessStreams
from ralph.testing._fake_async_process import FakeAsyncProcess
from ralph.testing._fake_popen import FakeImmortalPopen, FakePopen, FakeStubbornPopen
from ralph.testing._fake_psutil import FakePsutil
from ralph.testing._fake_psutil_process import FakePsutilProcess
from ralph.testing._fake_timeout_popen import FakeTimeoutPopen
from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ralph.process.manager._process_manager_types import (
        _AsyncProcessFactory as AsyncFactoryCallable,
    )
    from ralph.process.manager._process_manager_types import (
        _SyncProcessFactory as SyncFactoryCallable,
    )
    from ralph.process.manager._spawn_options import SpawnOptions


class FakeControllableAsyncProcess:
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
) -> SyncFactoryCallable:

    def factory(command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        del command, opts
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
) -> AsyncFactoryCallable:

    async def factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeAsyncProcess:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return FakeAsyncProcess(
            pid=next(pids),
            state=ProcessState(returncode=returncode),
        )

    return factory


def make_psutil_factory(
    processes: dict[int, FakePsutilProcess],
) -> FakePsutil:
    fake = FakePsutil()
    fake._processes = dict(processes)
    return fake


__all__ = [
    "AsyncProcessStreams",
    "FakeAsyncProcess",
    "FakeControllableAsyncProcess",
    "FakeImmortalPopen",
    "FakePopen",
    "FakePsutil",
    "FakePsutilProcess",
    "FakeStubbornPopen",
    "FakeTimeoutPopen",
    "ProcessState",
    "ProcessStreams",
    "make_async_process_factory",
    "make_psutil_factory",
    "make_sync_process_factory",
]
