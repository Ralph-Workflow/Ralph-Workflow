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

import socket
from typing import cast


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


__all__ = ["_reserve_listening_socket"]
