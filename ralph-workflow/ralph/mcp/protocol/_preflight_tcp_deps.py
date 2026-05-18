"""Injectable dependencies for TCP MCP server preflight probes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable
    from datetime import timedelta


@dataclass(frozen=True)
class PreflightTcpDeps:
    """Injectable dependencies for TCP MCP server preflight probes."""

    connect_to_endpoint_fn: Callable[[str, tuple[str, int], timedelta], socket.socket] | None = None
    list_tools_fn: Callable[[socket.socket, timedelta], list[str]] | None = None
