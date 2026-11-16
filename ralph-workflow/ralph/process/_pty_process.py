from __future__ import annotations

import os
import signal
import threading
import time

from ralph.process._suppress_close_error import _SuppressCloseError
from ralph.process._suppress_missing_process import _SuppressMissingProcess

_READ_CHUNK_SIZE = 4096


class PtyProcess:
    __slots__ = ("_closed", "_returncode", "_wait_event", "master_fd", "pid", "slave_fd")

    def __init__(
        self,
        pid: int,
        master_fd: int,
        slave_fd: int,
        _returncode: int | None = None,
        *,
        _closed: bool = False,
    ) -> None:
        self.pid = pid
        self.master_fd = master_fd
        self.slave_fd = slave_fd
        self._returncode = _returncode
        self._closed = _closed
        self._wait_event = threading.Event()

    @property
    def returncode(self) -> int | None:
        self.poll()
        return self._returncode

    def note_external_returncode(self, rc: int) -> None:
        if isinstance(rc, bool) or not isinstance(rc, int):
            raise TypeError(f"returncode must be int, got {type(rc).__name__}")
        if self._returncode is None:
            self._returncode = rc

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return self._returncode
        if pid == 0:
            return None
        self._returncode = _status_to_returncode(status)
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._returncode is not None:
            return self._returncode
        if timeout is None:
            _pid, status = os.waitpid(self.pid, 0)
            self._returncode = _status_to_returncode(status)
            return self._returncode

        deadline = time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if time.monotonic() >= deadline:
                raise TimeoutError from None
            self._wait_event.wait(0.01)

    def terminate(self) -> None:
        with _SuppressMissingProcess():
            os.kill(self.pid, signal.SIGTERM)

    def kill(self) -> None:
        with _SuppressMissingProcess():
            os.kill(self.pid, signal.SIGKILL)

    def read(self, max_bytes: int = _READ_CHUNK_SIZE) -> bytes:
        return os.read(self.master_fd, max_bytes)

    def fileno(self) -> int:
        return self.master_fd

    def isatty(self) -> bool:
        return os.isatty(self.master_fd)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in (self.master_fd, self.slave_fd):
            if fd >= 0:
                with _SuppressCloseError():
                    os.close(fd)


def _status_to_returncode(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return status
