from __future__ import annotations

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
    term = (os.environ if env is None else env).get("TERM", "")
    return bool(term and term != "dumb")


def terminal_restore_sequence() -> str:
    return "\x1b[?25h\x1b[0m"


def set_global_snapshot(modes: TermiosModes | None) -> None:
    _STATE.saved_modes = modes


def get_global_snapshot() -> TermiosModes | None:
    return _STATE.saved_modes


def snapshot_terminal_modes(fd: int | None = None) -> TermiosModes | None:
    try:
        target_fd = _resolve_fd(fd)
        if target_fd is None:
            return None
        modes: TermiosModes = termios.tcgetattr(target_fd)
        _STATE.saved_modes = modes
        return modes
    except Exception:
        return None


def restore_terminal_modes(
    fd: int | None = None,
    modes: TermiosModes | None = None,
) -> bool:
    target_modes = modes if modes is not None else _STATE.saved_modes
    if target_modes is None:
        return False
    try:
        target_fd = _resolve_fd(fd)
        if target_fd is None:
            return False
        termios.tcsetattr(target_fd, termios.TCSANOW, target_modes)
        return True
    except Exception:
        return False


def restore_terminal(
    *,
    stream: TextIO | object | None = None,
    modes: TermiosModes | None = None,
) -> None:
    try:
        target_stream, close_stream = (
            (stream, False) if stream is not None else _default_tty_stream()
        )
        is_tty = False
        try:
            if isinstance(target_stream, _IsATty):
                is_tty = bool(target_stream.isatty())
        except Exception:
            is_tty = False

        if is_tty:
            try:
                if terminal_understands_vt() and isinstance(target_stream, _Writable):
                    target_stream.write(terminal_restore_sequence())
                if isinstance(target_stream, _Flushable):
                    target_stream.flush()
            except Exception:
                pass

        target_fd = _fd_of(target_stream)
        restore_terminal_modes(fd=target_fd, modes=modes)
        _STATE.restored = True
        if close_stream and isinstance(target_stream, _Closeable):
            target_stream.close()
    except Exception:
        pass


def _default_tty_stream() -> tuple[TextIO | object | None, bool]:
    for candidate in (sys.stdout, sys.stderr):
        try:
            if isinstance(candidate, _IsATty) and candidate.isatty():
                return candidate, False
        except Exception:
            pass
    try:
        fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
        return (
            os.fdopen(fd, "w"),
            True,
        )
    except Exception:
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
    try:
        if isinstance(obj, _HasFileno):
            fn = obj.fileno()
            if isinstance(fn, int):
                return fn
    except Exception:
        pass
    return None


def _is_atty_fd(fd: int) -> bool:
    try:
        if hasattr(os, "isatty"):
            return os.isatty(fd)
    except Exception:
        pass
    return False
