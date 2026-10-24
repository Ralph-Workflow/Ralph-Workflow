"""LifecycleDeps — injectable dependencies for MCP server lifecycle management."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from socket import socket

    from ralph.mcp.protocol.startup import SessionLike
    from ralph.mcp.server._spawn_process import SpawnProcess

type PreflightFn = Callable[[str, list[str], timedelta], None]


@dataclass(frozen=True)
class LifecycleDeps:
    """Injectable dependencies for MCP server lifecycle management."""

    create_session_file: Callable[[Path, SessionLike], Path]
    subprocess_env: Callable[[Path], dict[str, str]]
    spawn_process: SpawnProcess
    preflight: PreflightFn
    preflight_timeout: Callable[[], timedelta]
    probe: Callable[[str, timedelta], None] | None = None
    probe_timeout: Callable[[], timedelta] | None = None
    # R1 hook: return (host, port, listening socket) the parent owns
    # for the bridge's full lifetime. The kernel backlog absorbs a
    # respawn instead of returning ``ECONNREFUSED`` to a queued
    # client. Every production caller wires this hook to
    # :func:`_reserve_listening_socket`.
    reserve_listening_socket: Callable[[], tuple[str, int, socket]] | None = None
    # Budget for the child's interpreter boot + imports, i.e. the time before it
    # records its first startup stage. ``None`` means no separate allowance:
    # the whole startup deadline is anchored at spawn. Production wires
    # :func:`mcp_boot_allowance_from_env`.
    boot_allowance: Callable[[], timedelta] | None = None


__all__ = ["LifecycleDeps", "PreflightFn"]
