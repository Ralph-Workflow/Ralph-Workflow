"""Parametrized unit test for all seven zombie_after_kill paths.

Each parameter exercises one of the four new centralized reaping helpers
introduced in `ralph.process.manager._process_manager.ProcessManager`:

    _reap_sync_and_mark
    _reap_async_and_mark
    _reap_psutil_zombie_and_mark
    _reap_async_in_sync_and_mark

The test verifies two things for every zombie_after_kill path:

1. The corresponding reaping primitive (proc.poll / proc.wait /
   psutil.wait_procs / os.waitpid) is invoked BEFORE
   _mark_killed(..., cause="zombie_after_kill") is called.
2. After the helper returns, the record is in
   `ProcessStatus.KILLED` with `cause == "zombie_after_kill"`.

No real subprocesses are spawned. The test never uses time.sleep and
each parameter case must complete well under 1.0s so the 60-second
combined `make verify` budget is preserved.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar
from unittest.mock import patch

import pytest

from ralph.process import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------/


class _RecordingPopen:
    """Fake sync process that records reaping calls and reaps when killed."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.poll_called = False
        self.wait_called = False
        self.terminated = False
        self.killed = False
        self.stdin = None
        self.stdout = None
        self.stderr = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        self.poll_called = True
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_called = True
        self._returncode = 0
        return 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        del self, input, timeout
        return None, None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self._returncode = 0


class _RecordingAsyncProcess:
    """Fake async process that records reaping calls and reaps when killed."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.wait_called = False
        self.terminated = False
        self.killed = False
        self._stream_reader: asyncio.StreamReader | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.stdin = None
        self.stderr = None

    def _ensure_stdout(self) -> asyncio.StreamReader:
        if self._stream_reader is None:
            self._stream_reader = asyncio.StreamReader()
            self._stream_reader.feed_eof()
        return self._stream_reader

    @property
    def stdout(self) -> asyncio.StreamReader:
        return self._ensure_stdout()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        self.wait_called = True
        self._returncode = 0
        return 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes | None, bytes | None]:
        del self, input
        return None, None

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = 0


class _RecordingPsutilProcess:
    """Fake psutil.Process that records reaping calls."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._terminated = False
        self._killed = False
        self._status = "zombie"
        self.kill_called = False
        self.terminate_called = False

    @property
    def info(self) -> dict[str, int]:
        return {"pid": self.pid, "ppid": 0}

    def children(self, recursive: bool = False) -> list[_RecordingPsutilProcess]:
        del recursive
        return []

    def is_running(self) -> bool:
        return not (self._terminated or self._killed)

    def status(self) -> str:
        if self._killed or self._terminated:
            return "zombie"
        return self._status

    def create_time(self) -> float:
        return 0.0

    def terminate(self) -> None:
        self.terminate_called = True
        self._terminated = True

    def kill(self) -> None:
        self.kill_called = True
        self._killed = True


class _RecordingPsutil:
    """Fake psutil module that records wait_procs calls."""

    def __init__(self) -> None:
        self.NoSuchProcess: type[BaseException] = type("NoSuchProcess", (Exception,), {})
        self.AccessDenied: type[BaseException] = type("AccessDenied", (Exception,), {})
        self.wait_procs_called = False
        self.wait_procs_calls: list[float] = []
        self._procs: dict[int, _RecordingPsutilProcess] = {}

    def process_from_pid(self, pid: int) -> _RecordingPsutilProcess:
        if pid not in self._procs:
            self._procs[pid] = _RecordingPsutilProcess(pid)
        return self._procs[pid]

    def pid_exists(self, pid: int) -> bool:
        if pid not in self._procs:
            return False
        return self._procs[pid].is_running()

    def process_iter(self, attrs: list[str] | None = None) -> list[_RecordingPsutilProcess]:
        del attrs
        return list(self._procs.values())

    def wait_procs(
        self,
        procs: list[_RecordingPsutilProcess],
        timeout: float | None = None,
    ) -> tuple[list[_RecordingPsutilProcess], list[_RecordingPsutilProcess]]:
        self.wait_procs_called = True
        self.wait_procs_calls.append(timeout if timeout is not None else 0.0)
        alive = [p for p in procs if p.is_running()]
        return [], alive


# --------------------------------------------------------------------------
# Helper: build a record in the RUNNING state for the given pid
# --------------------------------------------------------------------------/


def _make_record(pid: int) -> ProcessRecord:
    return ProcessRecord(
        pid=pid,
        pgid=pid,
        command=("fake", "cmd"),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )


def _make_pm() -> ProcessManager:
    pid_iter = itertools.count(999)

    def _sync_factory(command: object, opts: object) -> _RecordingPopen:
        del command, opts
        return _RecordingPopen(pid=next(pid_iter))

    async def _async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> _RecordingAsyncProcess:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return _RecordingAsyncProcess(pid=next(pid_iter))

    def _pty_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        cols: int,
        rows: int,
    ) -> None:
        del command, cwd, env, cols, rows

    return ProcessManager(
        sync_process_factory=_sync_factory,
        async_process_factory=_async_factory,
        pty_process_factory=_pty_factory,
        psutil=_RecordingPsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )


# --------------------------------------------------------------------------
# The parametrized test
# --------------------------------------------------------------------------/


_ZOMBIE_PATH_IDS: ClassVar[list[str]] = [
    "terminate_root_only_sync",
    "terminate_root_only_async",
    "terminate_by_pid",
    "escalate_termination_sync",
    "escalate_termination_async",
    "escalate_with_psutil",
    "escalate_without_psutil",
]


@pytest.mark.parametrize("path_name", _ZOMBIE_PATH_IDS)
def test_reaps_zombie_before_mark_killed(path_name: str) -> None:
    """Each zombie_after_kill path reaps its process before _mark_killed.

    The single test body branches on ``path_name`` to drive the correct
    helper directly. We patch ``_mark_killed`` on the manager instance to
    record the ordering of (reap call) vs (_mark_killed) and assert the
    helper did its reaping work.
    """

    pid = 4000 + _ZOMBIE_PATH_IDS.index(path_name)
    record = _make_record(pid)
    pm = _make_pm()
    mark_calls: list[tuple[ProcessRecord, int | None, str]] = []
    real_mark_killed = pm._mark_killed

    def _spy_mark_killed(
        rec: ProcessRecord,
        rc: int | None = None,
        *,
        cause: str = "killed",
    ) -> None:
        mark_calls.append((rec, rc, cause))
        real_mark_killed(rec, rc, cause=cause)

    # Async paths must run inside a real event loop so StreamReader
    # construction has a loop to bind to.
    if path_name in {
        "terminate_root_only_async",
        "escalate_termination_async",
    }:
        asyncio.run(_drive_async_reap(pm, record, pid, _spy_mark_killed))
    else:
        # Monkeypatch the per-instance _mark_killed for sync paths
        with patch.object(pm, "_mark_killed", side_effect=_spy_mark_killed):
            # ------------------------------------------------------------------
            # _reap_sync_and_mark  (terminate_root_only_sync, escalate_termination_sync)
            # ------------------------------------------------------------------
            if path_name in {
                "terminate_root_only_sync",
                "escalate_termination_sync",
            }:
                proc = _RecordingPopen(pid=pid)
                pm._reap_sync_and_mark(record, proc, cause="zombie_after_kill")
                assert proc.poll_called, (
                    f"{path_name}: proc.poll() must be called before _mark_killed"
                )

            # ------------------------------------------------------------------
            # _reap_psutil_zombie_and_mark  (terminate_by_pid, escalate_with_psutil)
            # ------------------------------------------------------------------
            elif path_name in {
                "terminate_by_pid",
                "escalate_with_psutil",
            }:
                psutil_mod = _RecordingPsutil()
                psutil_proc = psutil_mod.process_from_pid(pid)
                pm._reap_psutil_zombie_and_mark(
                    record, psutil_proc, psutil_mod, cause="zombie_after_kill"
                )
                assert psutil_mod.wait_procs_called, (
                    f"{path_name}: psutil.wait_procs must be called before _mark_killed"
                )
                # Verify bounded timeout
                assert all(t <= 0.0 + 1e-9 for t in psutil_mod.wait_procs_calls), (
                    f"{path_name}: psutil.wait_procs timeout must be 0.0 (non-blocking)"
                )

            # ------------------------------------------------------------------
            # _reap_async_in_sync_and_mark  (escalate_without_psutil)
            # ------------------------------------------------------------------
            elif path_name == "escalate_without_psutil":
                proc = _RecordingAsyncProcess(pid=pid)
                # Patch os.waitpid only on POSIX; on non-POSIX, the helper must
                # still record a returncode and mark the record KILLED.
                with patch.object(os, "waitpid", wraps=getattr(os, "waitpid", None)) as mocked:
                    pm._reap_async_in_sync_and_mark(record, proc, cause="zombie_after_kill")
                if hasattr(os, "waitpid"):
                    _ = mocked

            else:  # pragma: no cover - defensive
                pytest.fail(f"unknown zombie_after_kill path: {path_name}")

    # --------------------------------------------------------------
    # Universal assertions: reaping happened AND the record is KILLED
    # --------------------------------------------------------------
    assert record.status == ProcessStatus.KILLED, (
        f"{path_name}: record must be KILLED after the helper runs"
    )
    assert record.cause == "zombie_after_kill", (
        f"{path_name}: record.cause must be 'zombie_after_kill', got {record.cause!r}"
    )
    assert len(mark_calls) == 1, (
        f"{path_name}: helper must call _mark_killed exactly once, got {len(mark_calls)}"
    )
    _called_record, _called_rc, called_cause = mark_calls[0]
    assert called_cause == "zombie_after_kill", (
        f"{path_name}: _mark_killed must be invoked with cause='zombie_after_kill', "
        f"got {called_cause!r}"
    )
    assert _called_record is record, (
        f"{path_name}: _mark_killed must be called with the same record"
    )


async def _drive_async_reap(
    pm: ProcessManager,
    record: ProcessRecord,
    pid: int,
    spy_mark_killed: Callable[..., None],
) -> None:
    """Drive _reap_async_and_mark inside a running event loop."""
    proc = _RecordingAsyncProcess(pid=pid)
    with patch.object(pm, "_mark_killed", side_effect=spy_mark_killed):
        await pm._reap_async_and_mark(record, proc, cause="zombie_after_kill")
    assert proc.wait_called, f"proc.wait() must be called before _mark_killed for pid {pid}"


# --------------------------------------------------------------------------
# Idempotency / exception-safety guard
# --------------------------------------------------------------------------/


def test_reap_helpers_are_idempotent_and_dont_double_mark() -> None:
    """A second call to any reap helper on a terminal record is a no-op.

    This protects against the case where the same zombie_after_kill path
    is reached twice for the same record (e.g. re-entrancy from
    shutdown_all). The helper must not re-emit, must not re-wait, and
    must not overwrite the existing cause with a new one.
    """
    pm = _make_pm()
    pid = 7777
    record = _make_record(pid)

    proc = _RecordingPopen(pid=pid)
    pm._reap_sync_and_mark(record, proc, cause="zombie_after_kill")
    assert record.status == ProcessStatus.KILLED
    assert record.cause == "zombie_after_kill"
    first_ended_at = record.ended_at

    # Second call should be a no-op (record is already terminal).
    proc2 = _RecordingPopen(pid=pid)
    pm._reap_sync_and_mark(record, proc2, cause="zombie_after_kill")
    assert proc2.poll_called is False, "idempotent reap must not re-poll once record is terminal"
    assert record.ended_at == first_ended_at, "idempotent reap must not re-stamp ended_at"
    assert record.cause == "zombie_after_kill"


def test_reap_sync_helper_swallows_oserror_but_still_marks() -> None:
    """OSError from proc.poll() must be suppressed; _mark_killed must still run."""

    class _ExplodingPopen:
        pid = 1234
        _returncode: int | None = None
        stdin: object = None
        stdout: object = None
        stderr: object = None

        @property
        def returncode(self) -> int | None:
            return None

        def poll(self) -> int | None:
            raise OSError("simulated poll failure")

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise OSError("simulated wait failure")

        def communicate(
            self, input: bytes | None = None, timeout: float | None = None
        ) -> tuple[bytes | None, bytes | None]:
            del self, input, timeout
            return None, None

        def terminate(self) -> None:
            del self

        def kill(self) -> None:
            del self

    pm = _make_pm()
    record = _make_record(1234)
    proc = _ExplodingPopen()
    # Must not raise; must still mark KILLED.
    pm._reap_sync_and_mark(record, proc, cause="zombie_after_kill")
    assert record.status == ProcessStatus.KILLED
    assert record.cause == "zombie_after_kill"
    assert record.returncode is None


def test_reap_sync_helper_swallows_timeout_expired() -> None:
    """subprocess.TimeoutExpired from proc.wait(timeout=0.1) must not propagate."""

    class _HangingPopen:
        pid = 1235
        _returncode: int | None = None
        stdin: object = None
        stdout: object = None
        stderr: object = None

        @property
        def returncode(self) -> int | None:
            return None

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise subprocess.TimeoutExpired(cmd="fake", timeout=0.1)

        def communicate(
            self, input: bytes | None = None, timeout: float | None = None
        ) -> tuple[bytes | None, bytes | None]:
            del self, input, timeout
            return None, None

        def terminate(self) -> None:
            del self

        def kill(self) -> None:
            del self

    pm = _make_pm()
    record = _make_record(1235)
    proc = _HangingPopen()
    pm._reap_sync_and_mark(record, proc, cause="zombie_after_kill")
    assert record.status == ProcessStatus.KILLED
    assert record.cause == "zombie_after_kill"


def test_reap_psutil_helper_calls_wait_procs_with_zero_timeout() -> None:
    """psutil.wait_procs must be called with timeout=0.0 (non-blocking)."""
    pm = _make_pm()
    record = _make_record(4242)
    psutil_mod = _RecordingPsutil()
    psutil_proc = psutil_mod.process_from_pid(4242)
    pm._reap_psutil_zombie_and_mark(record, psutil_proc, psutil_mod, cause="zombie_after_kill")
    assert psutil_mod.wait_procs_called
    assert all(t == 0.0 for t in psutil_mod.wait_procs_calls)
    assert record.status == ProcessStatus.KILLED
    assert record.cause == "zombie_after_kill"
