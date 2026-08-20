"""Terminal state restoration utilities.

Provides safe, idempotent restoration of terminal modes (termios) and
escape sequence states (cursor visibility, alternate screen, mouse and focus
tracking, bracketed paste, cursor and keypad modes, scroll region, character
set, line wrap, SGR formatting).
"""

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
    """Return whether TERM permits VT control sequences.

    This is deliberately distinct from theme.py's Unicode-glyph TERM=dumb
    check: it protects terminal-control writes, not character selection.
    """
    term = (os.environ if env is None else env).get("TERM", "")
    return bool(term and term != "dumb")


def terminal_restore_sequence() -> str:
    """Return escape sequence to reset terminal display modes to standard state.

    Includes:
    - Show cursor (\\x1b[?25h)
    - Leave alternate screens (\\x1b[?1049l\\x1b[?1047l\\x1b[?47l)
    - Disable mouse reporting (\\x1b[?9l\\x1b[?1000l\\x1b[?1002l\\x1b[?1003l\\x1b[?1005l\\x1b[?1006l\\x1b[?1015l\\x1b[?1016l)
    - Disable synchronized output (\\x1b[?2026l)
    - Disable bracketed paste (\\x1b[?2004l)
    - Disable focus reporting (\\x1b[?1004l)
    - Restore normal cursor keys (\\x1b[?1l) and numeric keypad (\\x1b>)
    - Reset scroll region (\\x1b[r) and G0 charset to ASCII (\\x1b(B)
    - Enable autowrap (\\x1b[?7h)
    - Reset SGR attributes (\\x1b[0m)
    - Carriage return (\\r)
    """
    return (
        "\x1b[?25h"
        "\x1b[?1049l"
        "\x1b[?1047l"
        "\x1b[?47l"
        "\x1b[?9l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1005l\x1b[?1006l\x1b[?1015l\x1b[?1016l"
        "\x1b[?2026l"
        "\x1b[?2004l"
        "\x1b[?1004l"
        "\x1b[?1l"
        "\x1b>"
        "\x1b[r"
        "\x1b(B"
        "\x1b[?7h"
        "\x1b[0m"
        "\r"
    )


def set_global_snapshot(modes: TermiosModes | None) -> None:
    """Set or clear global saved termios modes snapshot."""
    _STATE.saved_modes = modes


def get_global_snapshot() -> TermiosModes | None:
    """Return the currently saved termios modes snapshot."""
    return _STATE.saved_modes


def snapshot_terminal_modes(fd: int | None = None) -> TermiosModes | None:
    """Capture current termios attributes for the given fd (or stdin/stdout/stderr).

    Returns None if termios is unavailable, fd is not a tty, or tcgetattr fails.
    """
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
    """Restore termios attributes on fd.

    Uses provided modes or global saved_modes.
    Returns True if successfully restored, False otherwise. Total-guarded.
    """
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
    """Idempotently restore terminal escape sequences and termios modes.

    Writes terminal_restore_sequence() to stream if stream is a tty.
    Restores termios modes using modes or saved snapshot.
    Swallows all exceptions.
    """
    try:
        target_stream, close_stream = (stream, False) if stream is not None else _default_tty_stream()
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
                target_fd = _fd_of(target_stream)
                if target_fd is not None and hasattr(termios, "tcflush") and hasattr(termios, "TCIFLUSH"):
                    termios.tcflush(target_fd, termios.TCIFLUSH)
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
        fd = os.open(  # resource-lifecycle-ok: wrapped stream is closed by restore_terminal after its one restore write; filesystem-write-ok: transient controlling-tty output fd, never persists content
            "/dev/tty", os.O_WRONLY | os.O_NOCTTY
        )
        return os.fdopen(  # filesystem-write-ok: transient controlling-tty output stream closed after one restore write
            fd, "w"
        ), True
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
