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
from typing import TYPE_CHECKING

from ralph.display.line_sanitizer import strip_terminal_control

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.context import DisplayContext


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
    """
    console = ctx.console

    def _sink(message: str) -> None:
        cleaned = strip_terminal_control(message.rstrip("\n"))
        console.print(cleaned, markup=False, highlight=False)

    return _sink


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
