
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

    create_session_file: Callable[[Path, SessionLike], Path]
    subprocess_env: Callable[[Path], dict[str, str]]
    spawn_process: SpawnProcess
    preflight: PreflightFn
    preflight_timeout: Callable[[], timedelta]
    probe: Callable[[str, timedelta], None] | None = None
    probe_timeout: Callable[[], timedelta] | None = None
    reserve_listening_socket: Callable[[], tuple[str, int, socket]] | None = None
    boot_allowance: Callable[[], timedelta] | None = None


__all__ = ["LifecycleDeps", "PreflightFn"]
