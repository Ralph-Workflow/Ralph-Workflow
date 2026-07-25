"""Ask the terminal what its actual background colour is (OSC 11).

Why a query rather than a guess
-------------------------------

A terminal background can be ANY colour -- Solarized Light's warm
cream, Solarized Dark's deep teal, Gruvbox's near-black brown, a
photo-backed transparent pane, a mid-tone slate. Classifying it from
``COLORFGBG`` alone is a guess: that variable is set by only a handful
of emulators, encodes an ANSI palette *index* rather than a colour, and
is routinely stale or absent. Picking highlight colours off a wrong
guess is exactly how a preview ends up unreadable.

XTerm's OSC 11 control sequence asks the emulator directly:

    write  ESC ] 1 1 ; ? BEL
    read   ESC ] 1 1 ; rgb:RRRR/GGGG/BBBB BEL      (or ST-terminated)

Every mainstream emulator implements it (xterm, iTerm2, Kitty, Alacritty,
WezTerm, foot, Ghostty, Windows Terminal, VTE-based terminals such as
GNOME Terminal). The reply is the *measured* background, so the caller
can compute its relative luminance and decide from data.

Safety contract
---------------

The probe touches the tty, so it is defensive by construction:

* It runs only when stdin AND stdout are both a tty, and never on
  Windows (no ``termios``).
* It puts the tty in raw mode inside a ``try/finally`` that always
  restores the original attributes, including on exception.
* It uses ``select`` with a short deadline (default 100 ms). A terminal
  that does not implement OSC 11 simply never replies and the probe
  returns ``None`` -- the caller then falls back to its env-based path.
* Every failure mode (no tty, background process, ``termios`` error,
  malformed reply) degrades to ``None``. It never raises.
* The result is cached for the process lifetime: the background does not
  change under us, and one 100 ms worst case at startup is the entire
  cost.
"""

from __future__ import annotations

import contextlib
import os
import re
import selectors
import sys
import time
from io import IOBase
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

#: OSC 11 background-colour query: ``ESC ] 1 1 ; ? BEL``.
_OSC11_QUERY: Final[str] = "\x1b]11;?\x07"

#: Reply body. Terminals answer with 1, 2, 3 or 4 hex digits per channel
#: (``rgb:fd/f6/e3`` and ``rgb:fdfd/f6f6/e3e3`` are both seen in the
#: wild), so each group is width-tolerant and normalised afterwards.
_OSC11_REPLY_RE: Final[re.Pattern[str]] = re.compile(
    r"rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})"
)

#: Seconds to wait for the terminal to answer before giving up.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 0.1

#: Bytes to read per ``os.read`` call while draining the reply.
_READ_CHUNK_BYTES: Final[int] = 64

#: Reply terminators: BEL or the two-byte String Terminator.
_REPLY_TERMINATORS: Final[tuple[str, ...]] = ("\x07", "\x1b\\")

_HEX_DIGITS_PER_BYTE: Final[int] = 2

#: Process-lifetime cache. ``False`` means "probe already ran and found
#: nothing"; a ``str`` is the resolved ``#RRGGBB``.
_cached_background: str | None | bool = False


def _scale_channel(raw: str) -> int:
    """Scale a 1-to-4 hex-digit channel value onto 0..255."""
    if len(raw) >= _HEX_DIGITS_PER_BYTE:
        # Take the most significant byte: 'fdfd' -> 'fd', 'fdf' -> 'fd'.
        return int(raw[:_HEX_DIGITS_PER_BYTE], 16)
    # Single digit: 'f' means full-scale for that width, so replicate it.
    return int(raw * _HEX_DIGITS_PER_BYTE, 16)


def parse_osc11_reply(reply: str) -> str | None:
    """Parse an OSC 11 reply into a ``#RRGGBB`` string.

    Accepts the ``rgb:R/G/B`` body with any per-channel hex width and
    ignores the surrounding OSC framing, so both BEL- and
    ST-terminated replies parse. Returns ``None`` when the text carries
    no recognisable colour.

    Parameters:
        reply: Raw text read back from the terminal.

    Returns:
        The background colour as ``#RRGGBB``, or ``None`` when the
        reply is absent or malformed.
    """
    match = _OSC11_REPLY_RE.search(reply)
    if match is None:
        return None
    red, green, blue = (_scale_channel(group) for group in match.groups())
    return f"#{red:02X}{green:02X}{blue:02X}"


def _wait_readable(fd: int, timeout: float) -> bool:
    """Return True when ``fd`` has data to read within ``timeout`` seconds."""
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        ready: Sequence[object] = selector.select(timeout)
        return bool(ready)


def _read_reply(fd: int, *, timeout: float) -> str:
    """Drain the terminal's reply from ``fd`` until a terminator or timeout."""
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


def _tty_fd() -> int | None:
    """Return the stdin fd when both stdin and stdout are a real tty."""
    stdin: object = sys.stdin
    stdout: object = sys.stdout
    if not isinstance(stdin, IOBase) or not isinstance(stdout, IOBase):
        return None
    try:
        if not (stdin.isatty() and stdout.isatty()):
            return None
        return stdin.fileno()
    except Exception:
        return None


def _probe(timeout: float) -> str | None:
    """Run the OSC 11 exchange once; ``None`` on any failure."""
    if sys.platform == "win32":
        return None
    fd = _tty_fd()
    if fd is None:
        return None

    import termios
    import tty

    try:
        original = termios.tcgetattr(fd)
    except Exception:
        # Not a real tty, or the process is backgrounded.
        return None
    try:
        tty.setraw(fd, termios.TCSANOW)
        sys.stdout.write(_OSC11_QUERY)
        sys.stdout.flush()
        reply = _read_reply(fd, timeout=timeout)
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            termios.tcsetattr(fd, termios.TCSADRAIN, original)
    return parse_osc11_reply(reply)


def query_terminal_background_hex(
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Return the terminal's background colour as ``#RRGGBB``.

    Sends the OSC 11 query once per process and caches the answer.
    Returns ``None`` whenever the background cannot be measured (not a
    tty, Windows, terminal does not implement OSC 11, reply timed out,
    malformed reply) so callers can fall back to an env-based heuristic.
    Never raises.

    Parameters:
        timeout: Seconds to wait for the terminal's reply.

    Returns:
        The measured background colour as ``#RRGGBB``, or ``None``.
    """
    global _cached_background  # noqa: PLW0603 - process-lifetime memo of an immutable probe
    if _cached_background is not False:
        return _cached_background  # type: ignore[return-value]
    result = _probe(timeout)
    _cached_background = result
    return result


def reset_cache() -> None:
    """Clear the memoised background so the next query re-probes.

    Exists for tests and for callers that deliberately want a fresh
    measurement; production code relies on the cache.
    """
    global _cached_background  # noqa: PLW0603 - test seam for the memo above
    _cached_background = False


__all__ = [
    "parse_osc11_reply",
    "query_terminal_background_hex",
    "reset_cache",
]
