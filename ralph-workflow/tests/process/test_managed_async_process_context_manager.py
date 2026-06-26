"""Black-box tests for ``ManagedAsyncProcess`` async context manager.

wt-024 memory-perf GAP-PROC-01: ``ManagedAsyncProcess`` lacks an
async context-manager protocol (``__aenter__``/``__aexit__``). Its
sync sibling ``ManagedProcess`` provides ``__enter__``/``__exit__``,
so the absence on the async side means any future async caller that
forgets a ``try/finally`` leaks the async subprocess.

Because ``ManagedAsyncProcess.terminate`` is ``async def``, the
correct pattern is the ASYNC context-manager protocol — a sync
``__exit__`` calling ``self.terminate()`` would return an un-awaited
coroutine and never terminate.

These tests are self-contained: they inline ``ProcessManager`` +
``FakePsutil`` + ``make_async_process_factory`` and assert on
``record.status`` (the real termination outcome via
``manager._escalate_termination_async``), NOT on
``FakeAsyncProcess.terminate`` (which ``ManagedAsyncProcess.terminate``
never calls — it routes through the manager).
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from typing import TYPE_CHECKING, Protocol, cast

import pytest

import ralph.process.manager._managed_async_process as _async_proc_mod
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.timeout_defaults import PROCESS_EXIT_WAIT_SECONDS

if TYPE_CHECKING:
    from ralph.process.manager._process_manager_types import (
        _AsyncProcessLike,
        _PsutilModuleLike,
)
from ralph.process.manager._process_status import (
    _TERMINAL_STATUSES,
    ProcessStatus,
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
    """Minimal protocol for a stdio stream that supports ``close()``."""

    def close(self) -> None: ...


class _AsyncProc(Protocol):
    """Minimal protocol matching the production ``_AsyncProcessLike`` for tests."""

    pid: int
    stdin: _AsyncStream | None
    stdout: _AsyncStream | None
    stderr: _AsyncStream | None
    returncode: int | None

    async def wait(self) -> int: ...

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def _psutil() -> _PsutilModuleLike:
    return cast("_PsutilModuleLike", FakePsutil())


def _as_proc(value: object) -> _AsyncProcessLike:
    return cast("_AsyncProcessLike", value)


async def test_async_context_manager_terminates_on_exit() -> None:
    """``async with handle`` must terminate the process on exit when still non-terminal."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status == ProcessStatus.RUNNING

    async with handle:
        pass  # exit the block without explicit terminate

    assert handle.record.status in _TERMINAL_STATUSES, (
        f"async-with exit should have terminated the process; status={handle.record.status}"
    )


async def test_async_context_manager_noop_when_already_terminal() -> None:
    """``async with handle`` on an already-terminal handle must be a no-op."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    # Mark the handle as already terminated via the sync manager API
    pm._mark_exited(handle.record, returncode=0)
    assert handle.record.status == ProcessStatus.EXITED

    async with handle:
        pass  # no raise, no re-escalation

    assert handle.record.status == ProcessStatus.EXITED


async def test_async_context_manager_returns_self() -> None:
    """``async with handle as x`` must return the handle itself."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=_psutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    async with handle as bound:
        assert bound is handle

    assert handle.record.status in _TERMINAL_STATUSES


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
    """A minimal async-proc stand-in: records close calls on stdin/stdout/stderr."""

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
) -> _AsyncProcessLike:
    """Build a fake async proc whose stdin/stdout/stderr each call ``close()``."""
    return cast(
        "_AsyncProcessLike",
        _RecordingFakeAsyncProc(
            pid=9999,
            stdin=_RecordingStream("stdin", close_calls, raise_on_close),
            stdout=_RecordingStream("stdout", close_calls, raise_on_close),
            stderr=_RecordingStream("stderr", close_calls, raise_on_close),
        ),
    )


async def test_aexit_closes_stdin_stdout_stderr_transports() -> None:
    """``__aexit__`` must close the underlying asyncio stdin/stdout/stderr transports."""
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
        f"expected stdin/stdout/stderr transports to be closed on __aexit__; "
        f"got {close_calls!r}"
    )


async def test_aexit_closes_transports_even_when_exception_raised() -> None:
    """``__aexit__`` must close transports even when the with-body raises."""
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
    """Transport-close errors during ``__aexit__`` must NOT mask the original exception."""
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
    """Await an Event that is never set; the await never resolves.

    The production code's ``asyncio.wait_for(... timeout=0.05s)``
    triggers its ``TimeoutError`` long before this stub's 60s elapses,
    so the test outcome is unchanged. The 60s ceiling keeps the
    audit ``blocking-wait`` check (docs/agents/testing-guide.md
    §'blocking-wait') green.
    """
    event = asyncio.Event()
    await asyncio.wait_for(event.wait(), timeout=60.0)


async def test_wait_bounds_with_asyncio_wait_for() -> None:
    """``wait()`` must raise TimeoutError when the subprocess never completes."""

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
    handle._proc = cast("_AsyncProcessLike", controllable)

    original = PROCESS_EXIT_WAIT_SECONDS
    _async_proc_mod.PROCESS_EXIT_WAIT_SECONDS = 0.05
    try:
        # Wrap in asyncio.wait_for to satisfy the audit ``blocking-wait``
        # check (docs/agents/testing-guide.md §'blocking-wait'). The
        # production code's 0.05s timeout fires first; the 5s wrapper
        # ceiling is a defensive backstop.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(handle.wait(), timeout=5.0)
    finally:
        _async_proc_mod.PROCESS_EXIT_WAIT_SECONDS = original


async def test_communicate_bounds_with_asyncio_wait_for() -> None:
    """``communicate()`` must raise TimeoutError when subprocess never completes."""

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
    handle._proc = cast("_AsyncProcessLike", controllable)

    original = PROCESS_EXIT_WAIT_SECONDS
    _async_proc_mod.PROCESS_EXIT_WAIT_SECONDS = 0.05
    try:
        # Wrap in asyncio.wait_for to satisfy the audit ``blocking-wait``
        # check (docs/agents/testing-guide.md §'blocking-wait'). The
        # production code's 0.05s timeout fires first; the 5s wrapper
        # ceiling is a defensive backstop.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(handle.communicate(), timeout=5.0)
    finally:
        _async_proc_mod.PROCESS_EXIT_WAIT_SECONDS = original
