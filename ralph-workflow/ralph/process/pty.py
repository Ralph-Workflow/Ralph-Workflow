"""POSIX PTY process primitives for unattended interactive runtimes.

This module owns the low-level pseudo-terminal spawn path used by transports that
must behave like a real interactive terminal session. The parent process keeps the
master file descriptor; the child gets the slave side as its controlling terminal.
"""

from __future__ import annotations

import errno
import fcntl
import os
import select
import signal
import struct
import termios
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_COLUMNS = 80
_DEFAULT_ROWS = 24
_READ_CHUNK_SIZE = 4096
_STDERR_FD = 2


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
        pid, status = os.waitpid(self.pid, os.WNOHANG)
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
            with _SuppressCloseError():
                os.close(fd)


class _SuppressMissingProcess:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc, tb
        return exc_type in (ProcessLookupError, PermissionError)


class _SuppressCloseError:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc, tb
        return exc_type is OSError


def spawn_pty_process(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    cols: int = _DEFAULT_COLUMNS,
    rows: int = _DEFAULT_ROWS,
) -> PtyProcess:
    """Spawn a child under a real PTY and return the parent-side handle."""

    if os.name == "nt":
        raise OSError("PTY-backed interactive Claude is supported only on POSIX platforms")

    master_fd, slave_fd = os.openpty()
    _set_winsize(slave_fd, rows=rows, cols=cols)
    pid = os.fork()
    if pid == 0:
        try:
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, _STDERR_FD)
            if slave_fd > _STDERR_FD:
                os.close(slave_fd)
            if cwd is not None:
                os.chdir(cwd)
            child_env = dict(os.environ)
            if env is not None:
                child_env.update(env)
            child_env.setdefault("TERM", "xterm-256color")
            os.execvpe(command[0], list(command), child_env)
        except BaseException:
            os._exit(127)

    _set_nonblocking(master_fd)
    return PtyProcess(pid=pid, master_fd=master_fd, slave_fd=slave_fd)


def wait_for_master_readable(master_fd: int, timeout_seconds: float) -> bool:
    """Return True when the PTY master has readable data within the timeout."""

    readable, _writable, _errors = select.select([master_fd], [], [], timeout_seconds)
    return bool(readable)


def read_master_chunk(master_fd: int, max_bytes: int = _READ_CHUNK_SIZE) -> bytes:
    """Read one chunk from the PTY master, tolerating EIO-on-EOF semantics."""

    try:
        return os.read(master_fd, max_bytes)
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


def _set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _set_winsize(fd: int, *, rows: int, cols: int) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def _status_to_returncode(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return status


__all__ = [
    "PtyProcess",
    "read_master_chunk",
    "spawn_pty_process",
    "wait_for_master_readable",
]
