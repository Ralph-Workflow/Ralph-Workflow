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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
    from typing import IO, Protocol

    class _PsutilProcessLike(Protocol):
        def children(self, recursive: bool = False) -> list[_PsutilProcessLike]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...
        def is_running(self) -> bool: ...
        def status(self) -> str: ...
        def create_time(self) -> float: ...

    class _PsutilModuleLike(Protocol):
        NoSuchProcess: type[BaseException]
        AccessDenied: type[BaseException]
        Process: Callable[[int], _PsutilProcessLike]

        def wait_procs(
            self,
            procs: list[_PsutilProcessLike],
            timeout: float | None = None,
        ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]: ...

psutil: _PsutilModuleLike | None
try:
    import psutil as _psutil
except ModuleNotFoundError:
    psutil = None
else:
    psutil = cast("_PsutilModuleLike", _psutil)


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


class ProcessTerminationError(RuntimeError):
    def __init__(self, pid: int, pgid: int) -> None:
        self.pid = pid
        self.pgid = pgid
        super().__init__(f"Process {pid} (pgid {pgid}) could not be terminated")


class ManagedProcess:
    """Wraps subprocess.Popen and integrates with ProcessManager lifecycle tracking."""

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
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
        """Return True when this process currently has live descendants.

        This is used by higher-level liveness checks so a quiet parent process is
        not mistaken for an idle one while spawned child work is still running.
        """
        if psutil is None:
            return False
        try:
            root = psutil.Process(self.pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

        for child in descendants:
            try:
                if child.is_running() and child.status() != "zombie":
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def descendant_snapshot(self) -> tuple[int, float | None]:
        """Return (live_descendant_count, oldest_age_seconds) excluding zombies.

        Returns:
            Tuple of (count of live non-zombie descendants,
                      age in seconds of the oldest live descendant, or None if none).
        """
        if psutil is None:
            return (0, None)
        try:
            root = psutil.Process(self.pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return (0, None)

        import time as _time  # noqa: PLC0415
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
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
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
        proc: asyncio.subprocess.Process,
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
    """

    def __init__(self, policy: ProcessManagerPolicy | None = None) -> None:
        self.policy = policy or ProcessManagerPolicy()
        self._records: dict[int, ProcessRecord] = {}
        self._sync_procs: dict[int, subprocess.Popen[bytes]] = {}
        self._listeners: dict[int, Callable[[ProcessEvent], None]] = {}
        self._listener_counter = 0
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
            proc: subprocess.Popen[bytes] = subprocess.Popen(
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
            proc = await asyncio.create_subprocess_exec(
                *cmd,
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

    def terminate(
        self,
        handle: ManagedProcess | ManagedAsyncProcess,
        *,
        grace_period_s: float | None = None,
    ) -> None:
        """Terminate a tracked process with escalation (sync version for ManagedProcess)."""
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
        proc: subprocess.Popen[bytes],
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
        proc: asyncio.subprocess.Process,
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
        """Kill a process tree given only its PID (no Popen handle available).

        Raises ProcessTerminationError if the process tree is still alive after
        escalating through graceful terminate → forceful kill.
        """
        if psutil is None:
            self._mark_killed(record)
            return

        try:
            root = psutil.Process(record.pid)
            children = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._mark_killed(record)
            return

        all_procs = [root, *children]
        for proc in all_procs:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                proc.terminate()

        _, alive = psutil.wait_procs(all_procs, timeout=grace_period_s)
        for proc in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                proc.kill()

        _, still_alive = psutil.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        self._mark_killed(record)
        if still_alive:
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(record.pid, record.pgid)

    def _escalate_termination_sync(
        self,
        record: ProcessRecord,
        proc: subprocess.Popen[bytes],
        grace_period_s: float,
    ) -> None:
        """Escalate termination for a ManagedProcess using psutil tree-walk."""
        if record.status in _TERMINAL_STATUSES:
            return

        if psutil is None:
            self._terminate_root_only_sync(record, proc, grace_period_s)
            return

        try:
            root = psutil.Process(record.pid)
            children = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._mark_killed(record, proc.poll())
            return

        all_procs = [root, *children]
        for p in all_procs:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.terminate()

        _, alive = psutil.wait_procs(all_procs, timeout=grace_period_s)
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.kill()

        _, still_alive = psutil.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        rc = proc.poll()
        self._mark_killed(record, rc)
        if still_alive:
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(record.pid, record.pgid)

    async def _escalate_termination_async(
        self,
        record: ProcessRecord,
        proc: asyncio.subprocess.Process,
        grace_period_s: float,
    ) -> None:
        """Escalate termination for a ManagedAsyncProcess using psutil tree-walk."""
        if record.status in _TERMINAL_STATUSES:
            return

        if psutil is None:
            await self._terminate_root_only_async(record, proc, grace_period_s)
            return

        pid = record.pid
        policy_kill = self.policy.kill_followup_timeout_s
        psutil_module = psutil
        assert psutil_module is not None

        def _do_terminate() -> bool:
            try:
                root = psutil_module.Process(pid)
                children = root.children(recursive=True)
            except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
                return False
            all_procs = [root, *children]
            for p in all_procs:
                with contextlib.suppress(psutil_module.NoSuchProcess, psutil_module.AccessDenied):
                    p.terminate()
            _, alive = psutil_module.wait_procs(all_procs, timeout=grace_period_s)
            for p in alive:
                with contextlib.suppress(psutil_module.NoSuchProcess, psutil_module.AccessDenied):
                    p.kill()
            _, still_alive = psutil_module.wait_procs(alive, timeout=policy_kill)
            return bool(still_alive)

        loop = asyncio.get_running_loop()
        still_alive = await loop.run_in_executor(None, _do_terminate)
        rc = proc.returncode
        self._mark_killed(record, rc)
        if still_alive:
            logger.error("Process {} still alive after kill", pid)
            raise ProcessTerminationError(record.pid, record.pgid)

    def _mark_exited(self, record: ProcessRecord, returncode: int | None) -> None:
        if record.status in _TERMINAL_STATUSES:
            return
        prev = record.status
        record.status = ProcessStatus.EXITED
        record.returncode = returncode
        record.ended_at = datetime.now(tz=UTC)
        record.cause = "exited"
        self._sync_procs.pop(record.pid, None)
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
        bound.debug(
            "process {} {} rc={}", record.pid, new_status.name, record.returncode
        )
    elif new_status == ProcessStatus.EXITED:
        bound.info(
            "process {} {} rc={}", record.pid, new_status.name, record.returncode
        )
    elif new_status == ProcessStatus.KILLED:
        bound.warning(
            "process {} {} rc={}", record.pid, new_status.name, record.returncode
        )
    elif new_status == ProcessStatus.FAILED:
        bound.error(
            "process {} {} rc={}", record.pid, new_status.name, record.returncode
        )


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
    """Context manager that tears down all processes labeled 'phase:<phase_name>' on exit.

    Logs a warning on ProcessTerminationError — cleanup always completes regardless.
    """
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
