"""ProcessManager — single source of truth for every child process Ralph spawns.

Invariants:
- Every spawned process is recorded from start to exit; no transitions are lost.
- All callers interact through spawn() / spawn_async() / terminate(); no direct
  subprocess.Popen / asyncio.create_subprocess_exec calls outside this module.
- Termination escalates: SIGTERM to the process group → poll for grace_period_s →
  SIGKILL the process group → wait kill_followup_timeout_s → raise if still alive.
- Listener exceptions never propagate into the spawn/terminate call path; they
  are logged via loguru and skipped.
- POSIX-only: os.killpg / start_new_session are used unconditionally.  Falls back
  to process.terminate() / process.kill() when os.killpg is unavailable (Windows).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import IO


class ProcessStatus(Enum):
    SPAWNED = auto()
    RUNNING = auto()
    EXITED = auto()
    KILLED = auto()
    FAILED = auto()


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
    def stdin(self) -> IO[bytes] | IO[str] | None:
        return self._proc.stdin

    @property
    def stdout(self) -> IO[bytes] | IO[str] | None:
        return self._proc.stdout

    @property
    def stderr(self) -> IO[bytes] | IO[str] | None:
        return self._proc.stderr

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def poll(self) -> int | None:
        rc = self._proc.poll()
        if rc is not None and self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
            self._manager._mark_exited(self._record, rc)
        return rc

    def wait(self, timeout: float | None = None) -> int:
        try:
            rc = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise
        if self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
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
        if rc is not None and self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
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

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
            self.terminate(grace_period_s=2.0)
        for attr in ("stdout", "stderr", "stdin"):
            pipe: IO[bytes] | IO[str] | None = getattr(self._proc, attr, None)
            if pipe is not None:
                with contextlib.suppress(Exception):
                    pipe.close()
        if self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
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
        if self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
            self._manager._mark_exited(self._record, rc)
        return rc

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        stdout, stderr = await self._proc.communicate(input)
        rc = self._proc.returncode
        if rc is not None and self._record.status not in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        ):
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
        _dead = (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED)
        for pid, record in list(self._records.items()):
            if record.status not in _dead:
                proc = self._sync_procs.get(pid)
                if proc is not None:
                    self._escalate_termination_sync(record, proc, gp)
                else:
                    self._killpg_record(record, gp)

    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        """Terminate all active processes whose label starts with label_prefix."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s
        _dead = (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED)
        for pid, record in list(self._records.items()):
            if (
                record.label is not None
                and record.label.startswith(label_prefix)
                and record.status not in _dead
            ):
                proc = self._sync_procs.get(pid)
                if proc is not None:
                    self._escalate_termination_sync(record, proc, gp)
                else:
                    self._killpg_record(record, gp)

    def _killpg_record(self, record: ProcessRecord, grace_period_s: float) -> None:
        """Kill a process group given only its record (no Popen handle available)."""
        if record.pgid <= 0:
            return
        try:
            _killpg(record.pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            self._mark_killed(record)
            return

        deadline = time.monotonic() + grace_period_s
        while time.monotonic() < deadline:
            try:
                os.kill(record.pid, 0)
            except (ProcessLookupError, PermissionError):
                self._mark_killed(record)
                return
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

        try:
            _killpg(record.pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            self._mark_killed(record)
            return

        deadline2 = time.monotonic() + self.policy.kill_followup_timeout_s
        while time.monotonic() < deadline2:
            try:
                os.kill(record.pid, 0)
            except (ProcessLookupError, PermissionError):
                self._mark_killed(record)
                return
            time.sleep(min(0.05, max(0, deadline2 - time.monotonic())))

        logger.error("Process {} (pgid {}) still alive after SIGKILL", record.pid, record.pgid)
        self._mark_killed(record)

    def _escalate_termination_sync(
        self,
        record: ProcessRecord,
        proc: subprocess.Popen[bytes],
        grace_period_s: float,
    ) -> None:
        """Escalate termination for a ManagedProcess (we have the Popen handle)."""
        if record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED):
            return

        with contextlib.suppress(ProcessLookupError, PermissionError):
            _killpg(record.pgid, signal.SIGTERM)

        deadline = time.monotonic() + grace_period_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self._mark_killed(record, proc.returncode)
                return
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

        with contextlib.suppress(ProcessLookupError, PermissionError):
            _killpg(record.pgid, signal.SIGKILL)

        deadline2 = time.monotonic() + self.policy.kill_followup_timeout_s
        while time.monotonic() < deadline2:
            if proc.poll() is not None:
                self._mark_killed(record, proc.returncode)
                return
            time.sleep(min(0.05, max(0, deadline2 - time.monotonic())))

        # Last attempt: blocking wait with short timeout
        try:
            proc.wait(timeout=0.1)
            self._mark_killed(record, proc.returncode)
            return
        except (subprocess.TimeoutExpired, Exception):
            pass

        logger.error("Process {} (pgid {}) still alive after SIGKILL", record.pid, record.pgid)
        self._mark_killed(record, proc.returncode)
        raise ProcessTerminationError(record.pid, record.pgid)

    async def _escalate_termination_async(
        self,
        record: ProcessRecord,
        proc: asyncio.subprocess.Process,
        grace_period_s: float,
    ) -> None:
        """Escalate termination for a ManagedAsyncProcess."""
        if record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED):
            return

        with contextlib.suppress(ProcessLookupError, PermissionError):
            _killpg(record.pgid, signal.SIGTERM)

        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_period_s)
            self._mark_killed(record, proc.returncode)
            return
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            pass

        with contextlib.suppress(ProcessLookupError, PermissionError):
            _killpg(record.pgid, signal.SIGKILL)

        try:
            await asyncio.wait_for(proc.wait(), timeout=self.policy.kill_followup_timeout_s)
            self._mark_killed(record, proc.returncode)
            return
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            pass

        logger.error("Process {} (pgid {}) still alive after SIGKILL", record.pid, record.pgid)
        self._mark_killed(record, proc.returncode)
        raise ProcessTerminationError(record.pid, record.pgid)

    def _mark_exited(self, record: ProcessRecord, returncode: int | None) -> None:
        if record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED):
            return
        prev = record.status
        record.status = ProcessStatus.EXITED
        record.returncode = returncode
        record.ended_at = datetime.now(tz=UTC)
        record.cause = "exited"
        self._sync_procs.pop(record.pid, None)
        self._emit(record, prev, ProcessStatus.EXITED)

    def _mark_killed(self, record: ProcessRecord, returncode: int | None = None) -> None:
        if record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED):
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


def _killpg(pgid: int, sig: signal.Signals) -> None:
    """Send signal to process group; falls back to os.kill when os.killpg is unavailable."""
    if hasattr(os, "killpg"):
        os.killpg(pgid, sig)
    else:
        os.kill(pgid, sig)


_singleton: ProcessManager | None = None


def get_process_manager(*, policy: ProcessManagerPolicy | None = None) -> ProcessManager:
    """Return the module-level ProcessManager singleton, creating it on first call."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = ProcessManager(policy=policy)
    return _singleton


def reset_process_manager() -> None:
    """Replace the singleton with a fresh instance.  Call from test teardown."""
    global _singleton  # noqa: PLW0603
    _singleton = None


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
    "reset_process_manager",
]
