"""R1 root-cause invariant: the endpoint never returns ``ECONNREFUSED`` to a queued client.

A respawned MCP child sits on its endpoint without listening — so
clients get ``ECONNREFUSED`` instead of being served. The fix: the
bridge holds ONE listening socket for its full lifetime, and every
spawned child inherits the FD by number, so a client that connects
during a respawn queues in the kernel backlog instead of being
refused.

This test exercises the production ``_reserve_listening_socket``
helper and the production ``_FallbackHttpServer`` constructor to
prove the end-to-end invariant: a foreign ``connect`` against the
inherited socket must NOT raise ``ECONNREFUSED`` even when the
server is not actively accepting.
"""

from __future__ import annotations

import socket

import pytest

from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._reserved_socket import _reserve_listening_socket

pytestmark = pytest.mark.subprocess_e2e


def _make_stub_handler() -> type:
    """Return a no-op HTTP handler class.

    The test never serves a real request — it only proves the
    listening socket is in LISTEN state. The handler therefore
    accepts the connection and immediately returns, so the
    ``handle_request`` call does not block.
    """

    class _StubHandler:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def log_message(self, *args: object, **kwargs: object) -> None:
            return

    return _StubHandler


def test_endpoint_never_refuses_after_inherited_socket_adoption() -> None:
    """A client ``connect`` against the inherited socket never ``ECONNREFUSED``."""
    host, port, sock = _reserve_listening_socket()
    try:
        server = _FallbackHttpServer(
            (host, port),
            _make_stub_handler(),
            parent_socket=sock,
        )
        try:
            # The server adopted the listening socket. A fresh
            # client connect must NOT see ``ECONNREFUSED`` — the
            # kernel has a live LISTEN entry, so the connect
            # either succeeds (when the accept loop runs) or
            # blocks on the backlog. We use a short timeout to
            # avoid hanging; the assertion is "no ECONNREFUSED".
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                client.settimeout(0.5)
                try:
                    client.connect((host, port))
                except OSError as exc:
                    if exc.errno in (socket.ECONNREFUSED,):
                        raise AssertionError(
                            f"inherited listening socket must not ECONNREFUSE"
                            f" a queued client; got errno={exc.errno} {exc!r}"
                        ) from exc
                    # ``ETIMEDOUT`` or ``EAGAIN`` are acceptable
                    # here: the kernel has the connection in the
                    # backlog and is waiting for the accept loop.
                    # The forbidden outcome is ``ECONNREFUSED``.
            finally:
                client.close()
        finally:
            server.server_close()
    finally:
        sock.close()


def test_endpoint_inherited_socket_is_listen_state() -> None:
    """The inherited socket is in LISTEN state when adopted (canonical kernel proof)."""
    host, port, sock = _reserve_listening_socket()
    try:
        server = _FallbackHttpServer(
            (host, port),
            _make_stub_handler(),
            parent_socket=sock,
        )
        try:
            assert server.socket is sock
            # ``SO_ACCEPTCONN`` is the canonical kernel proof of
            # LISTEN state. The constructor adopts the inherited
            # socket without re-binding, so the LISTEN state set
            # by ``_reserve_listening_socket`` is preserved.
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 0, (
                "inherited socket must be in LISTEN state;"
                " SO_ACCEPTCONN must be non-zero"
            )
        finally:
            server.server_close()
    finally:
        sock.close()
