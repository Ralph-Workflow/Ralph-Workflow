"""Terminal boundary for loguru records.

This module is the **terminal boundary** for log records. Every
loguru record that reaches Ralph's terminal flows through one of
these two sink factories, both of which:

  1. Strip every terminal-control construct via
     :func:`ralph.display.line_sanitizer.strip_terminal_control`
     (the single canonical stripper -- no module may define a
     second, narrower regex).
  2. Write through a single rendering surface so the rich
     ``Live`` status bar is the only painter of Ralph's terminal.

Two factories are exposed:

  - ``make_sanitizing_log_sink(ctx: DisplayContext)``: returns a
    loguru sink that prints through ``ctx.console`` with
    ``markup=False`` and ``highlight=False``. Use this from the CLI
    where a ``DisplayContext`` already owns a Console.

  - ``make_stderr_log_sink``: a fallback for worker / library
    callers that have no ``DisplayContext``. Still strips escapes
    before writing.

DI: this module MUST NOT construct a ``rich.console.Console``.
The single source of truth for Console construction is
``ralph.display.theme``. Take the Console from the injected
``DisplayContext`` (``ctx.console``) and pass an explicit
``writer=`` callback when building the stderr variant.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final

from ralph.display.line_sanitizer import strip_terminal_control

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.context import DisplayContext


#: Exceptions that mean the writing surface a sink was bound to is
#: gone for good: ``ValueError`` is what a closed ``TextIOBase``
#: raises ("I/O operation on closed file"), ``OSError`` covers
#: ``BrokenPipeError`` (``ralph run | head``) and a closed file
#: descriptor. A sink is bound to ONE surface for its whole life, so
#: neither condition can heal; retrying on every subsequent record
#: would make loguru's error interceptor dump a full Python traceback
#: to whatever ``sys.stderr`` happens to be current -- corrupting the
#: very output the record was meant to annotate.
_DEAD_SURFACE_ERRORS: Final[tuple[type[Exception], ...]] = (ValueError, OSError)


_LEVEL_STYLE_ROLES: Final[dict[str, str]] = {
    "TRACE": "theme.text.muted",
    "DEBUG": "theme.text.muted",
    "INFO": "theme.log.info",
    "SUCCESS": "theme.log.success",
    "MILESTONE": "theme.log.milestone",
    "WARNING": "theme.log.warn",
    "ERROR": "theme.log.error",
    "CRITICAL": "theme.log.error",
}


def _style_role_for_message(message: str) -> str:
    """Return the semantic theme role for a loguru message's level."""
    record: object = getattr(message, "record", None)
    if not isinstance(record, dict):
        return "theme.log.info"
    level: object = record.get("level")
    level_name: object = getattr(level, "name", None)
    if not isinstance(level_name, str):
        return "theme.log.info"
    return _LEVEL_STYLE_ROLES.get(level_name, "theme.log.info")


def _retiring_on_dead_surface(write: Callable[[str], None]) -> Callable[[str], None]:
    """Wrap ``write`` so it retires permanently once its surface dies.

    A loguru sink built over a *bound* surface (a ``Console`` that
    captured one file object) can outlive that surface: the file is
    closed while the loguru handler holding the sink is still
    registered. Every later record then raises out of ``emit`` and
    loguru's error interceptor prints ``--- Logging error in Loguru
    Handler #N ---`` plus a full traceback to the *current*
    ``sys.stderr`` -- which is a different stream, belonging to a
    different consumer, that never asked for it.

    Retiring on the first dead-surface error makes the sink
    self-limiting: a handler cannot keep writing to, or keep raising
    about, a stream that no longer exists. The remaining sinks (file
    sinks in particular) are unaffected, so records are still durably
    recorded even when the terminal surface has gone away.

    Args:
        write: The underlying single-argument writer to guard.

    Returns:
        A callable with the same signature that forwards to ``write``
        until the first :data:`_DEAD_SURFACE_ERRORS` failure, and is a
        no-op from then on.
    """
    live = True

    def _guarded(text: str) -> None:
        nonlocal live
        if not live:
            return
        try:
            write(text)
        except _DEAD_SURFACE_ERRORS:
            live = False

    return _guarded


def make_sanitizing_log_sink(ctx: DisplayContext) -> Callable[[str], None]:
    """Return a loguru sink that sanitizes via the DisplayContext Console.

    Args:
        ctx: DisplayContext that owns the Console. Its
            ``ctx.console.print(..., markup=False, highlight=False)``
            call is what makes the sink write through the rich Live
            region -- the same ``Console`` instance the status bar
            uses -- so the logger and the status bar are no longer
            two independent painters and Live's cursor-relative
            erases no longer wipe independent log lines.

    Returns:
        Callable accepting loguru's fully-formatted message string.
        The trailing newline added by loguru is stripped (rich adds
        its own), every terminal-control construct is removed via
        the canonical stripper, and the visible text is printed
        through the Console with ``markup=False`` and
        ``highlight=False`` so bracketed paths and ``[bold]``
        tokens survive verbatim.

        The Console is bound once, so the returned sink can outlive
        the stream that Console writes to. It therefore retires
        itself (see :func:`_retiring_on_dead_surface`) the first time
        that stream reports itself closed or broken, instead of
        raising out of loguru's ``emit`` and having loguru's error
        interceptor dump a traceback onto an unrelated
        ``sys.stderr``.
    """
    console = ctx.console

    def _sink(message: str) -> None:
        cleaned = strip_terminal_control(message.rstrip("\n"))
        console.print(
            cleaned,
            style=console.get_style(_style_role_for_message(message), default="none"),
            markup=False,
            highlight=False,
        )

    return _retiring_on_dead_surface(_sink)


def make_stderr_log_sink(
    *,
    writer: Callable[[str], None] | None = None,
) -> Callable[[str], None]:
    """Return a loguru sink that sanitizes before writing to ``sys.stderr``.

    Used by library / worker callers that have no ``DisplayContext``.
    Still strips escapes before writing; the writing surface is
    plain ``sys.stderr`` with a ``writer=`` override point for tests.

    Args:
        writer: Optional override for the writing surface. Defaults
            to ``sys.stderr.write``. Tests inject a ``StringIO.write``
            so they can assert against captured text without touching
            the real terminal.

    Returns:
        Callable accepting loguru's fully-formatted message string.
        Strips escapes, strips the trailing newline, and writes the
        sanitized text via ``writer`` (no rich markup interpretation).
    """
    # ``sys.stderr.write`` returns the number of bytes written (int); loguru
    # only requires a callable accepting a single string. Use a typed alias so
    # mypy accepts both the ``Callable[[str], None]`` contract for tests and
    # the ``sys.stderr.write`` fallback at the same time.
    sink_writer: Callable[[str], object]
    if writer is not None:
        sink_writer = writer
    else:

        def _stderr_writer(text: str) -> None:
            sys.stderr.write(text)
            sys.stderr.flush()

        sink_writer = _stderr_writer

    def _sink(message: str) -> None:
        cleaned = strip_terminal_control(message.rstrip("\n"))
        sink_writer(cleaned)

    return _sink


__all__ = ["make_sanitizing_log_sink", "make_stderr_log_sink"]
