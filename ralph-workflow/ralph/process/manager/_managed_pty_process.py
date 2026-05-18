"""ManagedPtyProcess — PTY-backed process wrapper with lifecycle integration."""

from __future__ import annotations

import contextlib
import time as _time
from typing import TYPE_CHECKING

from ralph.process.manager._process_status import _TERMINAL_STATUSES

if TYPE_CHECKING:
    from ralph.process.manager._process_manager import ProcessManager
    from ralph.process.manager._process_record import ProcessRecord

    class _PtyProcessLike:
        pid: int
        master_fd: int
        slave_fd: int

        @property
        def returncode(self) -> int | None: ...

        def poll(self) -> int | None: ...
        def wait(self, timeout: float | None = None) -> int: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...
        def close(self) -> None: ...
        def fileno(self) -> int: ...
        def isatty(self) -> bool: ...


class ManagedPtyProcess:
    """Wraps a PTY-backed process and exposes the master terminal handle."""

    def __init__(
        self,
        proc: _PtyProcessLike,
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
    def master_fd(self) -> int:
        return self._proc.master_fd

    @property
    def slave_fd(self) -> int:
        return self._proc.slave_fd

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
        except TimeoutError:
            raise
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    def fileno(self) -> int:
        return self._proc.fileno()

    def isatty(self) -> bool:
        return self._proc.isatty()

    def terminate(self, grace_period_s: float | None = None) -> None:
        gp = (
            grace_period_s
            if grace_period_s is not None
            else self._manager.policy.default_grace_period_s
        )
        self._manager._escalate_termination_pty(self._record, self._proc, gp)

    def kill(self) -> None:
        self._manager._escalate_termination_pty(self._record, self._proc, 0.0)

    def has_live_descendants(self) -> bool:
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

    def close(self) -> None:
        self._proc.close()

    def __enter__(self) -> ManagedPtyProcess:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        del exc_val, exc_tb
        if exc_type is KeyboardInterrupt:
            self.close()
            return
        if self._record.status not in _TERMINAL_STATUSES:
            self.terminate(grace_period_s=2.0)
        self.close()
        if self._record.status not in _TERMINAL_STATUSES:
            with contextlib.suppress(Exception):
                self._proc.wait()
