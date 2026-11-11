
from __future__ import annotations

import os
import socket
import stat
import time
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.mcp.protocol.env import MCP_INHERITED_FD_ENV
from ralph.mcp.server._mcp_server_error import McpServerError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def _reserve_listening_socket() -> tuple[str, int, socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    host = cast(
        "str", sock.getsockname()[0]
    )
    port = cast(
        "int", sock.getsockname()[1]
    )
    return host, port, sock


def _validate_inherited_fd(fd_int: int, env_name: str = MCP_INHERITED_FD_ENV) -> bool:
    try:
        st_mode = os.fstat(fd_int).st_mode
    except OSError as exc:
        logger.warning(
            "inherited_fd_invalid: env=%r fd=%r reason=%s",
            env_name,
            fd_int,
            exc,
        )
        return False
    if not stat.S_ISSOCK(st_mode):
        logger.warning(
            "inherited_fd_invalid: env=%r fd=%r reason=%s",
            env_name,
            fd_int,
            f"not a socket (st_mode=0o{st_mode:o})",
        )
        return False
    return True


def _adopt_inherited_socket(
    env_map: Mapping[str, str],
    t0: float,
    stage_fn: Callable[[str], None],
    clock_fn: Callable[[], float] = time.monotonic,
) -> socket.socket | None:
    if MCP_INHERITED_FD_ENV not in env_map:
        return None
    inherited_fd_raw = env_map[MCP_INHERITED_FD_ENV].strip()
    if not inherited_fd_raw:
        stage_fn("fd_inherit_failed")
        raise McpServerError(
            "inherited listening FD is empty: parent bridge handed a blank "
            "descriptor; cannot bind a fresh endpoint without breaking R1",
            restart_count=0,
            last_stage="fd_inherit_failed",
            elapsed_s=clock_fn() - t0,
        )
    if not inherited_fd_raw.isdigit():
        stage_fn("fd_inherit_failed")
        raise McpServerError(
            f"invalid inherited listening FD {inherited_fd_raw!r} "
            f"from parent bridge: expected non-negative integer",
            restart_count=0,
            last_stage="fd_inherit_failed",
            elapsed_s=clock_fn() - t0,
        )
    inherited_fd_int = int(inherited_fd_raw)
    if not _validate_inherited_fd(inherited_fd_int):
        stage_fn("fd_inherit_failed")
        raise McpServerError(
            f"inherited listening FD {inherited_fd_int} failed validation (not a live socket)",
            restart_count=0,
            last_stage="fd_inherit_failed",
            elapsed_s=clock_fn() - t0,
        )
    try:
        return socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
            fileno=inherited_fd_int,
        )
    except (OSError, ValueError) as exc:
        stage_fn("fd_inherit_failed")
        raise McpServerError(
            f"failed to adopt inherited listening FD "
            f"({inherited_fd_raw!r}) from parent bridge: {exc}",
            restart_count=0,
            last_stage="fd_inherit_failed",
            elapsed_s=clock_fn() - t0,
        ) from exc


__all__ = [
    "_adopt_inherited_socket",
    "_reserve_listening_socket",
    "_validate_inherited_fd",
]
