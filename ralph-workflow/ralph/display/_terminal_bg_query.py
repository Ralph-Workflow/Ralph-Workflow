from __future__ import annotations

import contextlib
import os
import re
import selectors
import sys
import time
from io import IOBase
from typing import TYPE_CHECKING, Final

from ralph.display.terminal_restore import (
    get_global_snapshot,
    set_global_snapshot,
    terminal_understands_vt,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_OSC11_QUERY: Final[str] = "\x1b]11;?\x07"

_OSC11_REPLY_RE: Final[re.Pattern[str]] = re.compile(
    r"rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})"
)

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 0.1

_READ_CHUNK_BYTES: Final[int] = 64

_REPLY_TERMINATORS: Final[tuple[str, ...]] = ("\x07", "\x1b\\")

_HEX_DIGITS_PER_BYTE: Final[int] = 2


class _BackgroundCacheState:
    background: str | None = None
    probed: bool = False


_BACKGROUND_CACHE = _BackgroundCacheState()


def _scale_channel(raw: str) -> int:
    if len(raw) >= _HEX_DIGITS_PER_BYTE:
        return int(raw[:_HEX_DIGITS_PER_BYTE], 16)
    return int(raw * _HEX_DIGITS_PER_BYTE, 16)


def parse_osc11_reply(reply: str) -> str | None:
    match = _OSC11_REPLY_RE.search(reply)
    if match is None:
        return None
    red, green, blue = (_scale_channel(group) for group in match.groups())
    return f"#{red:02X}{green:02X}{blue:02X}"


def _wait_readable(fd: int, timeout: float) -> bool:
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        ready: Sequence[object] = selector.select(timeout)
        return bool(ready)


def _read_reply(fd: int, *, timeout: float) -> str:
    buffer = ""
    deadline = time.monotonic() + timeout
    while True:
        remaining: float = deadline - time.monotonic()
        if remaining <= 0:
            return buffer
        if not _wait_readable(fd, remaining):
            return buffer
        chunk: bytes = os.read(fd, _READ_CHUNK_BYTES)
        if not chunk:
            return buffer
        buffer += chunk.decode("ascii", errors="replace")
        if any(terminator in buffer for terminator in _REPLY_TERMINATORS):
            return buffer


def _tty_fd() -> tuple[int, bool] | None:
    stdin: object = sys.stdin
    stdout: object = sys.stdout
    if not isinstance(stdout, IOBase):
        return None
    try:
        if not stdout.isatty():
            return None
        if isinstance(stdin, IOBase) and stdin.isatty():
            return stdin.fileno(), False
        return (
            os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY),
            True,
        )
    except Exception:
        return None


def _probe(timeout: float) -> tuple[bool, str | None]:
    if sys.platform == "win32":
        return False, None
    tty_fd = _tty_fd()
    if tty_fd is None:
        return False, None
    fd, close_fd = tty_fd

    import termios
    import tty

    previous = get_global_snapshot()
    try:
        original = termios.tcgetattr(fd)
        set_global_snapshot(original)
    except Exception:
        if close_fd:
            with contextlib.suppress(Exception):
                os.close(fd)
        return False, None
    parsed_color: str | None = None
    try:
        tty.setraw(fd, termios.TCSANOW)
        if terminal_understands_vt():
            os.write(fd, _OSC11_QUERY.encode())
            reply = _read_reply(fd, timeout=timeout)
            parsed_color = parse_osc11_reply(reply)
    except Exception:
        return True, None
    finally:
        with contextlib.suppress(Exception):
            if (
                parsed_color is None
                and hasattr(termios, "tcflush")
                and hasattr(termios, "TCIFLUSH")
            ):
                termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSADRAIN, original)
        set_global_snapshot(previous)
        if close_fd:
            with contextlib.suppress(Exception):
                os.close(fd)
    return True, parsed_color


def query_terminal_background_hex(
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    if _BACKGROUND_CACHE.probed:
        return _BACKGROUND_CACHE.background
    attempted, result = _probe(timeout)
    if attempted:
        _BACKGROUND_CACHE.background = result
        _BACKGROUND_CACHE.probed = True
    return result


def reset_cache() -> None:
    _BACKGROUND_CACHE.background = None
    _BACKGROUND_CACHE.probed = False


__all__ = [
    "parse_osc11_reply",
    "query_terminal_background_hex",
    "reset_cache",
]
