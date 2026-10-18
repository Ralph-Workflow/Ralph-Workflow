"""_reserve_listening_socket — parent-owned listening socket for the MCP bridge.

The bridge holds ONE listening socket for its full lifetime. Each
spawned child inherits the file descriptor by number (``pass_fds=``)
and serves on it without rebinding, so a client connecting while the
child is restarting queues in the kernel backlog instead of being
refused with ``ECONNREFUSED``.

This module deliberately does NOT use ``SO_REUSEADDR``: that flag
allows a foreign process to bind the same port while the bridge is
still alive, which would let it steal the bridge's endpoint. The
invariant — "from ``start_mcp_server`` returning until bridge shutdown,
no other socket can bind that port" — is enforced by the kernel
itself when the socket stays open in LISTEN state with no reuse flag.
"""

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
    """Bind a localhost listening socket and return (host, port, socket).

    The returned socket is in LISTEN state. The caller (the bridge)
    keeps it until shutdown; every spawned child inherits the
    descriptor and serves on it without re-binding.

    The host is hard-coded to ``"127.0.0.1"`` because the MCP bridge
    only ever exposes a loopback endpoint. An IPv6 host would
    incidentally work, but the wider bridge assumes the v4 string
    everywhere (it is part of the ``MCP_ENDPOINT_ENV`` contract).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # ``SO_REUSEADDR`` is intentionally NOT set: a foreign bind on the
    # same port must fail with ``EADDRINUSE`` while the bridge is
    # alive (the R1 invariant: "no other socket can bind the port").
    sock.bind(("127.0.0.1", 0))
    # Backlog of 128 matches ``ThreadingHTTPServer``'s default and
    # absorbs a burst of preflight retries without dropping the
    # listening descriptor.
    sock.listen(128)
    host = cast(
        "str", sock.getsockname()[0]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    port = cast(
        "int", sock.getsockname()[1]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return host, port, sock


def _validate_inherited_fd(fd_int: int, env_name: str = MCP_INHERITED_FD_ENV) -> bool:
    """Return True if ``fd_int`` refers to a live, socket-typed FD.

    Defensive guard: an invalid inherited FD crashes ``socket.socket``
    or points to a non-socket descriptor. When validation fails, the caller
    fails closed with typed ``McpServerError(last_stage="fd_inherit_failed")``.
    The check is an ``os.fstat`` confirming the FD's file type is ``S_IFSOCK``.
    """
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
    """Adopt inherited listening FD when set, failing closed if invalid."""
    if MCP_INHERITED_FD_ENV not in env_map:
        return None
    inherited_fd_raw = env_map[MCP_INHERITED_FD_ENV].strip()
    if not inherited_fd_raw:
        return None
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
