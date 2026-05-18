"""ManagedProcess — synchronous process wrapper with lifecycle integration."""

from __future__ import annotations

import contextlib
import subprocess
import time as _time
from typing import IO, TYPE_CHECKING

from ralph.process.manager._process_status import _TERMINAL_STATUSES

if TYPE_CHECKING:
    from ralph.process.manager._process_manager import ProcessManager
    from ralph.process.manager._process_manager_types import _SyncProcessLike
    from ralph.process.manager._process_record import ProcessRecord


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
            root = psutil_mod.process_from_pid(self.pid)
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
            root = psutil_mod.process_from_pid(self.pid)
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
