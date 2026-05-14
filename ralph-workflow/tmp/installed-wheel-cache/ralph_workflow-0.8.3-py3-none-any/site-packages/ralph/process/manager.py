"""ProcessManager — single source of truth for every child process Ralph spawns.

Invariants:

* Every spawned process is recorded from start to exit; no transitions are lost.
* All callers interact through ``spawn()``, ``spawn_async()``, or
  ``terminate()``; no direct ``subprocess.Popen`` or
  ``asyncio.create_subprocess_exec`` calls occur outside this module.
* Termination escalates via psutil: graceful ``terminate()`` to the process and
  all descendants, wait ``grace_period_s``, forceful ``kill()`` for survivors,
  wait ``kill_followup_timeout_s``, then raise ``ProcessTerminationError`` if
  anything is still alive.
* Cross-platform behavior relies on psutil for Linux, macOS, and Windows
  process-tree teardown without direct POSIX signal management.
* Listener exceptions never propagate into the spawn or terminate call path;
  they are logged via loguru and skipped.
* Lifecycle transitions are logged by default and therefore always observable.
* ``atexit`` guarantees no orphaned children at interpreter shutdown.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import subprocess
import time as _time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from typing import IO, TYPE_CHECKING, Protocol, cast

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence

    class _PsutilProcessLike(Protocol):
        def children(self, recursive: bool = False) -> Sequence[_PsutilProcessLike]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...
        def is_running(self) -> bool: ...
        def status(self) -> str: ...
        def create_time(self) -> float: ...

    class _PsutilModuleLike(Protocol):
        NoSuchProcess: type[BaseException]
        AccessDenied: type[BaseException]

        def Process(self, pid: int) -> _PsutilProcessLike: ...  # noqa: N802

        def wait_procs(
            self,
            procs: Sequence[_PsutilProcessLike],
            timeout: float | None = None,
        ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]: ...

    class _SyncProcessLike(Protocol):
        pid: int

        @property
        def stdin(self) -> IO[bytes] | None: ...

        @property
        def stdout(self) -> IO[bytes] | None: ...

        @property
        def stderr(self) -> IO[bytes] | None: ...

        @property
        def returncode(self) -> int | None: ...

        def poll(self) -> int | None: ...
        def wait(self, timeout: float | None = None) -> int: ...
        def communicate(
            self,
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes | None, bytes | None]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...

    class _AsyncProcessLike(Protocol):
        pid: int

        @property
        def stdin(self) -> asyncio.StreamWriter | None: ...

        @property
        def stdout(self) -> asyncio.StreamReader | None: ...

        @property
        def stderr(self) -> asyncio.StreamReader | None: ...

        @property
        def returncode(self) -> int | None: ...

        async def wait(self) -> int: ...
        async def communicate(
            self, input: bytes | None = None
        ) -> tuple[bytes | None, bytes | None]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...

    class _SyncProcessFactory(Protocol):
        def __call__(  # noqa: PLR0913
            self,
            command: Sequence[str],
            *,
            cwd: str | None,
            env: dict[str, str] | None,
            stdin: int | None,
            stdout: int | None,
            stderr: int | None,
            start_new_session: bool,
            text: bool,
        ) -> _SyncProcessLike: ...

    class _AsyncProcessFactory(Protocol):
        async def __call__(  # noqa: PLR0913
            self,
            command: Sequence[str],
            *,
            cwd: str | None,
            env: dict[str, str] | None,
            stdin: int | None,
            stdout: int | None,
            stderr: int | None,
            start_new_session: bool,
        ) -> _AsyncProcessLike: ...


psutil: _PsutilModuleLike | None = None
try:
    import psutil as _psutil
except ModuleNotFoundError:
    psutil = None
else:
    psutil = cast("_PsutilModuleLike", _psutil)


def _default_sync_process_factory(  # noqa: PLR0913
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    stdin: int | None,
    stdout: int | None,
    stderr: int | None,
    start_new_session: bool,
    text: bool,
) -> subprocess.Popen[bytes]:
    return cast(
        "subprocess.Popen[bytes]",
        subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=start_new_session,
            text=text,
        ),
    )


async def _default_async_process_factory(  # noqa: PLR0913
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    stdin: int | None,
    stdout: int | None,
    stderr: int | None,
    start_new_session: bool,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=start_new_session,
    )


class ProcessStatus(Enum):
    SPAWNED = auto()
    RUNNING = auto()
    EXITED = auto()
    KILLED = auto()
    FAILED = auto()


_TERMINAL_STATUSES = (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED)


@dataclass
class ProcessRecord:
    pid: int
    pgid: int
    command: tuple[str, ...]
    cwd: str | None
    started_at: datetime
    status: ProcessStatus
    returncode: int | None = None
    ended_at: datetime | None = None
    cause: str | None = None
    failure_message: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class ProcessEvent:
    record: ProcessRecord
    previous_status: ProcessStatus
    new_status: ProcessStatus
    timestamp: datetime


@dataclass(frozen=True)
class ProcessManagerPolicy:
    default_grace_period_s: float = 5.0
    kill_followup_timeout_s: float = 2.0
    log_events: bool = True
    terminal_history_limit: int = 256


class ProcessTerminationError(RuntimeError):
    def __init__(self, pid: int, pgid: int) -> None:
        self.pid = pid
        self.pgid = pgid
        super().__init__(f"Process {pid} (pgid {pgid}) could not be terminated")


class ManagedProcess:
    """Wraps a synchronous process handle and integrates with ProcessManager lifecycle tracking."""

    def __init__(
        self,
        proc: _SyncProcessLike,
        record: ProcessRecord,
        manager: ProcessManager,
    ) -> None:
        self._proc = proc
        self._record = record
        self._manager = manager

    @property
    def record(self) -> ProcessRecord:
        return self._record

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def stdin(self) -> IO[bytes] | None:
        return self._proc.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self._proc.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self._proc.stderr

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def poll(self) -> int | None:
        rc = self._proc.poll()
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    def wait(self, timeout: float | None = None) -> int:
        try:
            rc = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None]:
        try:
            stdout, stderr = self._proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise
        rc = self._proc.returncode
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return stdout, stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        gp = (
            grace_period_s
            if grace_period_s is not None
            else self._manager.policy.default_grace_period_s
        )
        self._manager._escalate_termination_sync(self._record, self._proc, gp)

    def kill(self) -> None:
        self._manager._escalate_termination_sync(self._record, self._proc, 0.0)

    def has_live_descendants(self) -> bool:
        """Return True when this process currently has live descendants."""
        psutil_mod = self._manager._psutil
        if psutil_mod is None:
            return False
        try:
            root = psutil_mod.Process(self.pid)
            descendants = root.children(recursive=True)
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            return False

        for child in descendants:
            try:
                if child.is_running() and child.status() != "zombie":
                    return True
            except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                continue
        return False

    def descendant_snapshot(self) -> tuple[int, float | None]:
        """Return (live_descendant_count, oldest_age_seconds) excluding zombies."""
        psutil_mod = self._manager._psutil
        if psutil_mod is None:
            return (0, None)
        try:
            root = psutil_mod.Process(self.pid)
            descendants = root.children(recursive=True)
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            return (0, None)

        now = _time.monotonic()
        count = 0
        oldest_age: float | None = None
        for child in descendants:
            try:
                if not (child.is_running() and child.status() != "zombie"):
                    continue
                count += 1
                try:
                    create_time = child.create_time()
                    age = now - create_time
                    if oldest_age is None or age > oldest_age:
                        oldest_age = age
                except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    pass
            except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                continue
        return (count, oldest_age)

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._record.status not in _TERMINAL_STATUSES:
            self.terminate(grace_period_s=2.0)
        for attr in ("stdout", "stderr", "stdin"):
            pipe: IO[bytes] | IO[str] | None = getattr(self._proc, attr, None)
            if pipe is not None:
                with contextlib.suppress(Exception):
                    pipe.close()
        if self._record.status not in _TERMINAL_STATUSES:
            with contextlib.suppress(Exception):
                self._proc.wait()


class ManagedAsyncProcess:
    """Wraps asyncio.subprocess.Process and integrates with ProcessManager lifecycle tracking."""

    def __init__(
        self,
        proc: _AsyncProcessLike,
        record: ProcessRecord,
        manager: ProcessManager,
    ) -> None:
        self._proc = proc
        self._record = record
        self._manager = manager

    @property
    def record(self) -> ProcessRecord:
        return self._record

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self._proc.stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return self._proc.stderr

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        return self._proc.stdin

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    async def wait(self) -> int:
        rc = await self._proc.wait()
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        stdout, stderr = await self._proc.communicate(input)
        rc = self._proc.returncode
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return stdout or b"", stderr or b""

    async def terminate(self, grace_period_s: float | None = None) -> None:
        gp = (
            grace_period_s
            if grace_period_s is not None
            else self._manager.policy.default_grace_period_s
        )
        await self._manager._escalate_termination_async(self._record, self._proc, gp)


class ProcessManager:
    """Single source of truth for all child processes Ralph spawns.

    Termination uses psutil for cross-platform process-tree teardown: graceful
    terminate() to the root and all descendants, then forceful kill() to
    survivors. Works identically on Linux, macOS, and Windows.

    Use :func:`get_process_manager` to obtain the module-level singleton.
    Inject a custom instance (with a test-friendly :class:`ProcessManagerPolicy`)
    to keep tests fast and isolated.

    For testing, inject custom process factories and psutil module via the
    constructor to avoid spawning real processes::

        from ralph.testing.fake_process import (
            FakePopen, FakeAsyncProcess, FakePsutil,
            make_sync_process_factory, make_async_process_factory,
        )
        import itertools

        pm = ProcessManager(
            policy=_FAST_POLICY,
            sync_process_factory=make_sync_process_factory(itertools.count(1)),
            async_process_factory=make_async_process_factory(itertools.count(1)),
            psutil=FakePsutil(),
        )
    """

    def __init__(
        self,
        policy: ProcessManagerPolicy | None = None,
        sync_process_factory: _SyncProcessFactory | None = None,
        async_process_factory: _AsyncProcessFactory | None = None,
        psutil: _PsutilModuleLike | None = None,
    ) -> None:
        self.policy = policy or ProcessManagerPolicy()
        self._records: dict[int, ProcessRecord] = {}
        self._terminal_records: OrderedDict[int, ProcessRecord] = OrderedDict()
        self._sync_procs: dict[int, _SyncProcessLike] = {}
        self._listeners: dict[int, Callable[[ProcessEvent], None]] = {}
        self._listener_counter = 0
        self._sync_process_factory = sync_process_factory or _default_sync_process_factory
        self._async_process_factory = async_process_factory or _default_async_process_factory
        self._psutil = psutil
        if self.policy.log_events:
            self.register_listener(_loguru_event_listener)

    def register_listener(self, callback: Callable[[ProcessEvent], None]) -> Callable[[], None]:
        """Subscribe to lifecycle events.  Returns an unsubscribe callable."""
        lid = self._listener_counter
        self._listener_counter += 1
        self._listeners[lid] = callback

        def unsubscribe() -> None:
            self._listeners.pop(lid, None)

        return unsubscribe

    def spawn(  # noqa: PLR0913
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        start_new_session: bool = True,
        label: str | None = None,
        text: bool = False,
    ) -> ManagedProcess:
        """Spawn a synchronous child process and begin tracking it."""
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        try:
            proc: _SyncProcessLike = self._sync_process_factory(
                cmd,
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=start_new_session,
                text=text,
            )
        except OSError as exc:
            record = ProcessRecord(
                pid=-1,
                pgid=-1,
                command=cmd,
                cwd=cwd,
                started_at=now,
                status=ProcessStatus.FAILED,
                ended_at=datetime.now(tz=UTC),
                cause="failed",
                failure_message=str(exc),
                label=label,
            )
            self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.FAILED)
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=label,
        )
        self._terminal_records.pop(pid, None)
        self._records[pid] = record
        self._sync_procs[pid] = proc
        self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        return ManagedProcess(proc, record, self)

    async def spawn_async(  # noqa: PLR0913
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        start_new_session: bool = True,
        label: str | None = None,
    ) -> ManagedAsyncProcess:
        """Spawn an async child process and begin tracking it."""
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        try:
            proc = await self._async_process_factory(
                cmd,
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=start_new_session,
            )
        except OSError as exc:
            record = ProcessRecord(
                pid=-1,
                pgid=-1,
                command=cmd,
                cwd=cwd,
                started_at=now,
                status=ProcessStatus.FAILED,
                ended_at=datetime.now(tz=UTC),
                cause="failed",
                failure_message=str(exc),
                label=label,
            )
            self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.FAILED)
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=label,
        )
        self._terminal_records.pop(pid, None)
        self._records[pid] = record
        self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        return ManagedAsyncProcess(proc, record, self)

    def list_active(self) -> list[ProcessRecord]:
        """Return all records for processes that have not yet terminated."""
        return [
            r
            for r in self._records.values()
            if r.status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        ]

    def get_record(self, pid: int, *, include_terminal: bool = True) -> ProcessRecord | None:
        """Return a tracked record by pid, preferring active records over terminal history."""
        record = self._records.get(pid)
        if record is not None:
            return record
        if not include_terminal:
            return None
        return self._terminal_records.get(pid)

    def list_records(
        self,
        *,
        include_active: bool = True,
        include_terminal: bool = False,
        label_prefix: str | None = None,
    ) -> list[ProcessRecord]:
        """Return active and/or terminal records with optional label filtering."""
        records: list[ProcessRecord] = []
        if include_active:
            records.extend(self._records.values())
        if include_terminal:
            records.extend(self._terminal_records.values())
        if label_prefix is None:
            return list(records)
        return [
            record
            for record in records
            if record.label is not None and record.label.startswith(label_prefix)
        ]

    def terminate(
        self,
        handle: ManagedProcess | ManagedAsyncProcess,
        *,
        grace_period_s: float | None = None,
    ) -> None:
        """Terminate a tracked process with escalation."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s
        if isinstance(handle, ManagedProcess):
            self._escalate_termination_sync(handle.record, handle._proc, gp)

    def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
        """Terminate all active processes."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s
        for pid, record in list(self._records.items()):
            if record.status not in _TERMINAL_STATUSES:
                proc = self._sync_procs.get(pid)
                if proc is not None:
                    self._escalate_termination_sync(record, proc, gp)
                else:
                    self._terminate_by_pid(record, gp)

    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        """Terminate all active processes whose label starts with label_prefix."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s
        for pid, record in list(self._records.items()):
            if (
                record.label is not None
                and record.label.startswith(label_prefix)
                and record.status not in _TERMINAL_STATUSES
            ):
                proc = self._sync_procs.get(pid)
                if proc is not None:
                    self._escalate_termination_sync(record, proc, gp)
                else:
                    self._terminate_by_pid(record, gp)

    def _terminate_root_only_sync(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike,
        grace_period_s: float,
    ) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            rc = proc.wait(timeout=grace_period_s)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            try:
                rc = proc.wait(timeout=self.policy.kill_followup_timeout_s)
            except subprocess.TimeoutExpired:
                self._mark_killed(record, proc.poll())
                logger.error("Process {} still alive after kill", record.pid)
                raise ProcessTerminationError(record.pid, record.pgid) from None
        self._mark_killed(record, rc)

    async def _terminate_root_only_async(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        grace_period_s: float,
    ) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=grace_period_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            try:
                rc = await asyncio.wait_for(
                    proc.wait(), timeout=self.policy.kill_followup_timeout_s
                )
            except TimeoutError:
                self._mark_killed(record, proc.returncode)
                logger.error("Process {} still alive after kill", record.pid)
                raise ProcessTerminationError(record.pid, record.pgid) from None
        self._mark_killed(record, rc)

    def _terminate_by_pid(self, record: ProcessRecord, grace_period_s: float) -> None:
        """Kill a process tree given only its PID (no Popen handle available)."""
        psutil_mod = self._psutil
        if psutil_mod is None:
            self._mark_killed(record)
            return

        try:
            root = psutil_mod.Process(record.pid)
            children = root.children(recursive=True)
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            self._mark_killed(record)
            return

        all_procs = [root, *children]
        for proc in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.terminate()

        _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
        for proc in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.kill()

        _, still_alive = psutil_mod.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        self._mark_killed(record)
        if still_alive:
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(record.pid, record.pgid)

    def _escalate_termination_sync(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike,
        grace_period_s: float,
    ) -> None:
        """Escalate termination for a ManagedProcess using psutil tree-walk."""
        if record.status in _TERMINAL_STATUSES:
            return

        psutil_mod = self._psutil
        if psutil_mod is None:
            self._terminate_root_only_sync(record, proc, grace_period_s)
            return

        try:
            root = psutil_mod.Process(record.pid)
            children = root.children(recursive=True)
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            self._mark_killed(record, proc.poll())
            return

        all_procs = [root, *children]
        for p in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.terminate()

        _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
        for p in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.kill()

        _, still_alive = psutil_mod.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        rc = proc.poll()
        self._mark_killed(record, rc)
        if still_alive:
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(record.pid, record.pgid)

    async def _escalate_termination_async(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        grace_period_s: float,
    ) -> None:
        """Escalate termination for a ManagedAsyncProcess using psutil tree-walk."""
        if record.status in _TERMINAL_STATUSES:
            return

        psutil_mod = self._psutil
        if psutil_mod is None:
            await self._terminate_root_only_async(record, proc, grace_period_s)
            return

        pid = record.pid
        policy_kill = self.policy.kill_followup_timeout_s

        def _do_terminate() -> bool:
            try:
                root = psutil_mod.Process(pid)
                children = root.children(recursive=True)
            except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                return False
            all_procs = [root, *children]
            for p in all_procs:
                with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    p.terminate()
            _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
            for p in alive:
                with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    p.kill()
            _, still_alive = psutil_mod.wait_procs(alive, timeout=policy_kill)
            return bool(still_alive)

        loop = asyncio.get_running_loop()
        still_alive = await loop.run_in_executor(None, _do_terminate)
        rc = proc.returncode
        self._mark_killed(record, rc)
        if still_alive:
            logger.error("Process {} still alive after kill", pid)
            raise ProcessTerminationError(record.pid, record.pgid)

    def _record_terminal_state(self, record: ProcessRecord) -> None:
        self._records.pop(record.pid, None)
        limit = max(self.policy.terminal_history_limit, 0)
        if limit == 0:
            self._terminal_records.clear()
            return
        self._terminal_records.pop(record.pid, None)
        self._terminal_records[record.pid] = record
        while len(self._terminal_records) > limit:
            self._terminal_records.popitem(last=False)

    def _mark_exited(self, record: ProcessRecord, returncode: int | None) -> None:
        if record.status in _TERMINAL_STATUSES:
            return
        prev = record.status
        record.status = ProcessStatus.EXITED
        record.returncode = returncode
        record.ended_at = datetime.now(tz=UTC)
        record.cause = "exited"
        self._sync_procs.pop(record.pid, None)
        self._record_terminal_state(record)
        self._emit(record, prev, ProcessStatus.EXITED)

    def _mark_killed(self, record: ProcessRecord, returncode: int | None = None) -> None:
        if record.status in _TERMINAL_STATUSES:
            return
        prev = record.status
        record.status = ProcessStatus.KILLED
        record.returncode = returncode
        record.ended_at = datetime.now(tz=UTC)
        record.cause = "killed"
        self._sync_procs.pop(record.pid, None)
        self._record_terminal_state(record)
        self._emit(record, prev, ProcessStatus.KILLED)

    def _emit(
        self,
        record: ProcessRecord,
        previous_status: ProcessStatus,
        new_status: ProcessStatus,
    ) -> None:
        event = ProcessEvent(
            record=record,
            previous_status=previous_status,
            new_status=new_status,
            timestamp=datetime.now(tz=UTC),
        )
        for callback in list(self._listeners.values()):
            try:
                callback(event)
            except Exception:
                logger.exception("ProcessManager listener raised an exception")


def _loguru_event_listener(event: ProcessEvent) -> None:
    """Default listener: log process lifecycle transitions via loguru."""
    record = event.record
    new_status = event.new_status
    bound = logger.bind(component="process", pid=record.pid, label=record.label)
    if new_status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING):
        bound.debug("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.EXITED:
        bound.info("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.KILLED:
        bound.warning("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.FAILED:
        bound.error("process {} {} rc={}", record.pid, new_status.name, record.returncode)


_singleton: ProcessManager | None = None
_atexit_registered: bool = False


def _atexit_shutdown() -> None:
    """Last-resort safety net: terminate all tracked children at interpreter exit."""
    try:
        pm = _singleton
        if pm is None:
            return
        pm.shutdown_all(grace_period_s=0.5)
    except BaseException:
        pass


def get_process_manager(*, policy: ProcessManagerPolicy | None = None) -> ProcessManager:
    """Return the module-level ProcessManager singleton, creating it on first call."""
    global _singleton, _atexit_registered  # noqa: PLW0603
    if _singleton is None:
        _singleton = ProcessManager(policy=policy)
    if not _atexit_registered:
        atexit.register(_atexit_shutdown)
        _atexit_registered = True
    return _singleton


def reset_process_manager() -> None:
    """Replace the singleton with a fresh instance.  Call from test teardown."""
    global _singleton  # noqa: PLW0603
    _singleton = None


@contextmanager
def process_phase_scope(phase_name: str) -> Generator[None, None, None]:
    """Context manager that tears down all processes labeled 'phase:<phase_name>' on exit."""
    try:
        yield
    finally:
        try:
            get_process_manager().shutdown_all_for_label(
                f"phase:{phase_name}",
                grace_period_s=get_process_manager().policy.default_grace_period_s,
            )
        except ProcessTerminationError as exc:
            logger.warning(
                "phase:{} cleanup could not terminate all processes: {}", phase_name, exc
            )


__all__ = [
    "ManagedAsyncProcess",
    "ManagedProcess",
    "ProcessEvent",
    "ProcessManager",
    "ProcessManagerPolicy",
    "ProcessRecord",
    "ProcessStatus",
    "ProcessTerminationError",
    "get_process_manager",
    "process_phase_scope",
    "reset_process_manager",
]
