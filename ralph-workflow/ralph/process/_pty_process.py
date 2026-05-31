"""PTY process handle for the parent side of a pseudo-terminal."""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass

from ralph.process._suppress_close_error import _SuppressCloseError
from ralph.process._suppress_missing_process import _SuppressMissingProcess

_READ_CHUNK_SIZE = 4096


@dataclass
class PtyProcess:
    """Tracked PTY child process owned by the parent master file descriptor."""

    pid: int
    master_fd: int
    slave_fd: int
    _returncode: int | None = None
    _closed: bool = False

    @property
    def returncode(self) -> int | None:
        self.poll()
        return self._returncode

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
            try:
                _pid, status = os.waitpid(self.pid, 0)
            except ChildProcessError:
                raise
            self._returncode = _status_to_returncode(status)
            return self._returncode

        deadline = time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if time.monotonic() >= deadline:
                raise TimeoutError from None
            time.sleep(0.01)

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
