from __future__ import annotations

import asyncio
import itertools
import sys
from typing import TYPE_CHECKING, Protocol

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy

if TYPE_CHECKING:
    from ralph.process.manager import (
        AsyncProcessLike,
        PsutilModuleLike,
    )
from ralph.process.manager import (
    ProcessStatus,
    is_terminal_status,
)
from ralph.testing.fake_process import (
    FakeControllableAsyncProcess,
    FakePsutil,
    make_async_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)


class _AsyncStream(Protocol):
    def close(self) -> None: ...


class _AsyncProc(Protocol):
    pid: int
    stdin: _AsyncStream | None
    stdout: _AsyncStream | None
    stderr: _AsyncStream | None
    returncode: int | None

    async def wait(self) -> int: ...

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def _psutil() -> PsutilModuleLike:
    return FakePsutil()


def _as_proc(value: object) -> AsyncProcessLike:
    return value


async def test_async_context_manager_terminates_on_exit() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status == ProcessStatus.RUNNING

    async with handle:
        pass

    assert is_terminal_status(handle.record.status), (
        f"async-with exit should have terminated the process; status={handle.record.status}"
    )


async def test_async_context_manager_noop_when_already_terminal() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    pm._mark_exited(handle.record, returncode=0)
    assert handle.record.status == ProcessStatus.EXITED

    async with handle:
        pass

    assert handle.record.status == ProcessStatus.EXITED


async def test_async_context_manager_returns_self() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    async with handle as bound:
        assert bound is handle

    assert is_terminal_status(handle.record.status)


class _RecordingStream:
    def __init__(self, name: str, calls: list[str], raise_on_close: bool) -> None:
        self._name = name
        self._calls = calls
        self._raise_on_close = raise_on_close

    def close(self) -> None:
        self._calls.append(self._name)
        if self._raise_on_close:
            raise OSError(f"{self._name} boom")


class _RecordingFakeAsyncProc:
    def __init__(
        self,
        pid: int,
        *,
        stdin: _RecordingStream,
        stdout: _RecordingStream,
        stderr: _RecordingStream,
    ) -> None:
        self._pid = pid
        self._stdin: _RecordingStream = stdin
        self._stdout: _RecordingStream = stdout
        self._stderr: _RecordingStream = stderr
        self._returncode: int | None = None

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def stdin(self) -> _RecordingStream:
        return self._stdin

    @property
    def stdout(self) -> _RecordingStream:
        return self._stdout

    @property
    def stderr(self) -> _RecordingStream:
        return self._stderr

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        return 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        return (b"", b"")

    def terminate(self) -> None:
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


def _build_proc_with_close_tracking(
    close_calls: list[str], *, raise_on_close: bool
) -> AsyncProcessLike:
    return _RecordingFakeAsyncProc(
        pid=9999,
        stdin=_RecordingStream("stdin", close_calls, raise_on_close),
        stdout=_RecordingStream("stdout", close_calls, raise_on_close),
        stderr=_RecordingStream("stderr", close_calls, raise_on_close),
    )


async def test_aexit_closes_stdin_stdout_stderr_transports() -> None:
    close_calls: list[str] = []
    fake_proc = _build_proc_with_close_tracking(close_calls, raise_on_close=False)

    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    handle._proc = fake_proc

    async with handle:
        pass

    assert sorted(close_calls) == ["stderr", "stdin", "stdout"], (
        f"expected stdin/stdout/stderr transports to be closed on __aexit__; got {close_calls!r}"
    )


async def test_aexit_closes_transports_even_when_exception_raised() -> None:
    close_calls: list[str] = []
    fake_proc = _build_proc_with_close_tracking(close_calls, raise_on_close=False)

    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    handle._proc = fake_proc

    with pytest.raises(RuntimeError, match="boom"):
        async with handle:
            raise RuntimeError("boom")

    assert sorted(close_calls) == ["stderr", "stdin", "stdout"], (
        f"expected stdin/stdout/stderr transports to be closed on __aexit__ even "
        f"when body raises; got {close_calls!r}"
    )


async def test_aexit_swallows_close_errors() -> None:
    close_calls: list[str] = []
    fake_proc = _build_proc_with_close_tracking(close_calls, raise_on_close=True)

    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    handle._proc = fake_proc

    with pytest.raises(RuntimeError, match="primary"):
        async with handle:
            raise RuntimeError("primary")


async def _never_return_future() -> None:
    event = asyncio.Event()
    await asyncio.wait_for(event.wait(), timeout=60.0)


async def test_wait_bounds_with_asyncio_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:

    class _NeverWaitProc(FakeControllableAsyncProcess):
        async def wait(self) -> int:
            await _never_return_future()
            return 0

    controllable = _NeverWaitProc(pid=4500, returncode=0)

    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    handle._proc = controllable

    monkeypatch.setattr("ralph.timeout_defaults.PROCESS_EXIT_WAIT_SECONDS", 0.05)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(handle.wait(), timeout=5.0)


async def test_communicate_bounds_with_asyncio_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:

    class _NeverCommunicateProc(FakeControllableAsyncProcess):
        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            del input
            await _never_return_future()
            return (b"", b"")

    controllable = _NeverCommunicateProc(pid=4600, returncode=0)

    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    handle._proc = controllable

    monkeypatch.setattr("ralph.timeout_defaults.PROCESS_EXIT_WAIT_SECONDS", 0.05)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(handle.communicate(), timeout=5.0)
