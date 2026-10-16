"""R1 root-cause invariant: the endpoint never returns ``ECONNREFUSED`` to a queued client."""

from __future__ import annotations

import socket

import pytest

from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._reserved_socket import _reserve_listening_socket

pytestmark = pytest.mark.subprocess_e2e


def _make_stub_handler() -> type:
    class _StubHandler:
        def __init__(self, *args, **kwargs):
            pass

        def log_message(self, *args, **kwargs):
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
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 0, (
                "inherited socket must be in LISTEN state;"
                " SO_ACCEPTCONN must be non-zero"
            )
        finally:
            server.server_close()
    finally:
        sock.close()
