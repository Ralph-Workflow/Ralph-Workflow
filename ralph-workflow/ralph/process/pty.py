
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import select
import struct
import termios
from typing import TYPE_CHECKING

from ralph.process._pty_process import PtyProcess
from ralph.process._spawn_env import child_env_for_spawn

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_COLUMNS = 80
_DEFAULT_ROWS = 24
_READ_CHUNK_SIZE = 4096
_STDERR_FD = 2
_STAGE_SETUP = "setup"
_STAGE_CHDIR = "chdir"
_STAGE_EXEC = "exec"


def spawn_pty_process(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    cols: int = _DEFAULT_COLUMNS,
    rows: int = _DEFAULT_ROWS,
) -> PtyProcess:

    if os.name == "nt":
        raise OSError("PTY-backed interactive Claude is supported only on POSIX platforms")

    master_fd, slave_fd = os.openpty()
    _set_winsize(slave_fd, rows=rows, cols=cols)
    ready_read_fd, ready_write_fd = os.pipe()
    try:
        pid = os.fork()
    except OSError:
        for fd in (master_fd, slave_fd, ready_read_fd, ready_write_fd):
            os.close(fd)
        raise
    if pid == 0:
        stage = _STAGE_SETUP
        try:
            os.close(master_fd)
            os.close(ready_read_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, _STDERR_FD)
            if slave_fd > _STDERR_FD:
                os.close(slave_fd)
            if cwd is not None:
                stage = _STAGE_CHDIR
                os.chdir(cwd)
            child_env = child_env_for_spawn(env)
            child_env.setdefault("TERM", "xterm-256color")
            stage = _STAGE_EXEC
            os.execvpe(command[0], list(command), child_env)
        except BaseException as exc:
            _report_child_failure(ready_write_fd, stage, exc)
        os._exit(127)

    os.close(slave_fd)
    os.close(ready_write_fd)
    try:
        report = _read_child_report(ready_read_fd)
    finally:
        os.close(ready_read_fd)
    if report:
        os.close(master_fd)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
        raise _child_failure(report, command=command, cwd=cwd)
    _set_nonblocking(master_fd)
    return PtyProcess(pid=pid, master_fd=master_fd, slave_fd=-1)


def _report_child_failure(fd: int, stage: str, exc: BaseException) -> None:
    code = exc.errno if isinstance(exc, OSError) and exc.errno is not None else 0
    with contextlib.suppress(BaseException):
        os.write(fd, f"{stage}:{code}".encode("ascii"))


def _read_child_report(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, _READ_CHUNK_SIZE)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _child_failure(report: bytes, *, command: Sequence[str], cwd: str | None) -> OSError:
    stage, _sep, code_text = report.decode("ascii", errors="replace").partition(":")
    code = int(code_text) if code_text.isdigit() else 0
    if code == 0:
        return OSError(f"PTY child failed during {stage} before exec of {command[0]!r}")
    filename = cwd if stage == _STAGE_CHDIR else command[0] if stage == _STAGE_EXEC else None
    return OSError(code, os.strerror(code), filename)


def wait_for_master_readable(master_fd: int, timeout_seconds: float) -> bool:

    poller = select.poll()
    poller.register(master_fd, select.POLLIN)
    events = poller.poll(max(0.0, timeout_seconds) * 1000.0)
    if any(mask & select.POLLNVAL for _fd, mask in events):
        raise OSError(errno.EBADF, os.strerror(errno.EBADF))
    return bool(events)


def read_master_chunk(master_fd: int, max_bytes: int = _READ_CHUNK_SIZE) -> bytes:

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
