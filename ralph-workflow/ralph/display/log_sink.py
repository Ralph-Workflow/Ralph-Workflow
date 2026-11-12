from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final

from ralph.display.line_sanitizer import strip_terminal_control

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.context import DisplayContext


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
    record: object = getattr(message, "record", None)
    if not isinstance(record, dict):
        return "theme.log.info"
    level: object = record.get("level")
    level_name: object = getattr(level, "name", None)
    if not isinstance(level_name, str):
        return "theme.log.info"
    return _LEVEL_STYLE_ROLES.get(level_name, "theme.log.info")


_MAX_RECORD_LINE_CHARS: Final = 1000
_MAX_RECORD_LINES: Final = 40


def _clip_line(line: str) -> str:
    if len(line) <= _MAX_RECORD_LINE_CHARS:
        return line
    extra = len(line) - _MAX_RECORD_LINE_CHARS
    return f"{line[:_MAX_RECORD_LINE_CHARS]} ... [+{extra} chars omitted]"


def _bound_record(text: str) -> str:
    lines = text.split("\n")
    bounded = [_clip_line(line) for line in lines[:_MAX_RECORD_LINES]]
    if len(lines) > _MAX_RECORD_LINES:
        bounded.append(f"... [+{len(lines) - _MAX_RECORD_LINES} more lines omitted]")
    return "\n".join(bounded)


def _retiring_on_dead_surface(write: Callable[[str], None]) -> Callable[[str], None]:
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
    console = ctx.console

    def _sink(message: str) -> None:
        cleaned = strip_terminal_control(message.rstrip("\n"))
        console.print(
            _bound_record(cleaned),
            style=console.get_style(_style_role_for_message(message), default="none"),
            markup=False,
            highlight=False,
        )

    return _retiring_on_dead_surface(_sink)


def make_stderr_log_sink(
    *,
    writer: Callable[[str], None] | None = None,
) -> Callable[[str], None]:
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
        sink_writer(_bound_record(cleaned) + "\n")

    return _sink


__all__ = ["make_sanitizing_log_sink", "make_stderr_log_sink"]
