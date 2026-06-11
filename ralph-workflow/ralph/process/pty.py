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
import struct
import termios
from typing import TYPE_CHECKING

from ralph.process._pty_process import PtyProcess

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_COLUMNS = 80
_DEFAULT_ROWS = 24
_READ_CHUNK_SIZE = 4096
_STDERR_FD = 2


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
    # Process forked via ProcessManager.spawn_pty — see ralph.process.manager.ProcessManager
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

    os.close(slave_fd)
    _set_nonblocking(master_fd)
    return PtyProcess(pid=pid, master_fd=master_fd, slave_fd=-1)


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


__all__ = [
    "PtyProcess",
    "read_master_chunk",
    "spawn_pty_process",
    "wait_for_master_readable",
]
