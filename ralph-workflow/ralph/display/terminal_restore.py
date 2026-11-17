from __future__ import annotations

import contextlib
import os
import sys
import termios
from typing import TYPE_CHECKING, Protocol, TextIO, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

TermiosModes = list[int | list[bytes | int]]


class _State:
    saved_modes: TermiosModes | None = None
    restored: bool = False


_STATE = _State()


@runtime_checkable
class _IsATty(Protocol):
    def isatty(self) -> bool: ...


@runtime_checkable
class _Writable(Protocol):
    def write(self, s: str, /) -> int: ...


@runtime_checkable
class _Flushable(Protocol):
    def flush(self) -> None: ...


@runtime_checkable
class _Closeable(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class _HasFileno(Protocol):
    def fileno(self) -> int: ...


def terminal_understands_vt(env: Mapping[str, str] | None = None) -> bool:
    from ralph.config._env import getenv

    term = env.get("TERM", "") if env is not None else (getenv("TERM") or "")
    return bool(term and term != "dumb")


def terminal_restore_sequence() -> str:
    return "\x1b[?25h\x1b[0m"


def set_global_snapshot(modes: TermiosModes | None) -> None:
    _STATE.saved_modes = modes


def get_global_snapshot() -> TermiosModes | None:
    return _STATE.saved_modes


def snapshot_terminal_modes(fd: int | None = None) -> TermiosModes | None:
    target_fd = _resolve_fd(fd)
    if target_fd is None:
        return None
    try:
        modes: TermiosModes = termios.tcgetattr(target_fd)
    except (termios.error, OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return None
    else:
        _STATE.saved_modes = modes
        return modes


def restore_terminal_modes(
    fd: int | None = None,
    modes: TermiosModes | None = None,
) -> bool:
    target_modes = modes if modes is not None else _STATE.saved_modes
    if target_modes is None:
        return False
    target_fd = _resolve_fd(fd)
    if target_fd is None:
        return False
    try:
        termios.tcsetattr(target_fd, termios.TCSANOW, target_modes)
    except (termios.error, OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return False
    else:
        return True


def restore_terminal(
    *,
    stream: TextIO | object | None = None,
    modes: TermiosModes | None = None,
) -> None:
    with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
        target_stream, close_stream = (
            (stream, False) if stream is not None else _default_tty_stream()
        )
        is_tty = False
        with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
            if isinstance(target_stream, _IsATty):
                is_tty = bool(target_stream.isatty())

        if is_tty:
            with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
                if terminal_understands_vt() and isinstance(target_stream, _Writable):
                    target_stream.write(terminal_restore_sequence())
                if isinstance(target_stream, _Flushable):
                    target_stream.flush()

        target_fd = _fd_of(target_stream)
        restore_terminal_modes(fd=target_fd, modes=modes)
        _STATE.restored = True
        if close_stream and isinstance(target_stream, _Closeable):
            target_stream.close()


def _default_tty_stream() -> tuple[TextIO | object | None, bool]:
    for candidate in (sys.stdout, sys.stderr):
        with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
            if isinstance(candidate, _IsATty) and candidate.isatty():
                return candidate, False
    try:
        fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
        return (
            os.fdopen(fd, "w"),
            True,
        )
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return None, False


def _resolve_fd(fd: int | None) -> int | None:
    if fd is not None:
        return fd if _is_atty_fd(fd) else None
    for candidate in (sys.stdin, sys.stdout, sys.stderr):
        c_fd = _fd_of(candidate)
        if c_fd is not None and _is_atty_fd(c_fd):
            return c_fd
    return None


def _fd_of(obj: object) -> int | None:
    if isinstance(obj, int):
        return obj
    with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
        if isinstance(obj, _HasFileno):
            fn = obj.fileno()
            if isinstance(fn, int):
                return fn
    return None


def _is_atty_fd(fd: int) -> bool:
    with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
        if hasattr(os, "isatty"):
            return os.isatty(fd)
    return False
