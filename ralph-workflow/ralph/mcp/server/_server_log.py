
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_LOG_DIR_PARTS = (".agent", "tmp")
_LOG_FILENAME = "mcp-server.log"

_MAX_DIAGNOSTIC_CHARS = 4_000


def mcp_server_log_path(root: Path) -> Path:

    return root.joinpath(*_LOG_DIR_PARTS, _LOG_FILENAME)


def mcp_server_log_size(root: Path) -> int:

    try:
        return mcp_server_log_path(root).stat().st_size
    except OSError:
        return 0


def read_mcp_server_log_since(root: Path, offset: int) -> str:

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
