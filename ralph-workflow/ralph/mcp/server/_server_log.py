"""The MCP server subprocess's diagnostic log: one path, one bounded reader.

The subprocess's stdout and stderr are redirected to a single append-only file
so a crash inside it leaves a trace. Nothing READ that file, though, so a child
that died during startup -- an unreachable custom MCP server, a bad import, a
port already taken -- surfaced to the operator as a bare ``[Errno 61] Connection
refused``: the symptom, never the cause.

The spawn path records the file's size before starting the child and, if the
child never becomes ready, reads only the bytes appended since. Reading from an
offset (rather than tailing the whole file) matters because the log is shared
across restarts: a tail would replay a PREVIOUS crash and misattribute it to
this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_LOG_DIR_PARTS = (".agent", "tmp")
_LOG_FILENAME = "mcp-server.log"

# Enough for a Python traceback plus the exception line that matters, bounded so
# a subprocess that logged megabytes before dying cannot flood the error path.
_MAX_DIAGNOSTIC_CHARS = 4_000


def mcp_server_log_path(root: Path) -> Path:
    """Return the append-only log the MCP server subprocess writes under ``root``."""

    return root.joinpath(*_LOG_DIR_PARTS, _LOG_FILENAME)


def mcp_server_log_size(root: Path) -> int:
    """Return the current size of the subprocess log, or 0 when it does not exist."""

    try:
        return mcp_server_log_path(root).stat().st_size
    except OSError:
        return 0


def read_mcp_server_log_since(root: Path, offset: int) -> str:
    """Return the output appended to the subprocess log after ``offset``.

    Returns an empty string when the file is absent, unreadable, or has not
    grown -- a missing diagnostic must never replace the caller's own error.
    """

    path = mcp_server_log_path(root)
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            appended = stream.read()
    except OSError:
        return ""
    text = appended.decode("utf-8", errors="replace").strip()
    if len(text) > _MAX_DIAGNOSTIC_CHARS:
        return "...\n" + text[-_MAX_DIAGNOSTIC_CHARS:]
    return text


__all__ = [
    "mcp_server_log_path",
    "mcp_server_log_size",
    "read_mcp_server_log_since",
]
