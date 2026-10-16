"""R1 invariant: a child with a delayed pre-serve stage still passes preflight."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.subprocess_e2e


import socket
import threading
import time

from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._reserved_socket import _reserve_listening_socket


def _make_blocking_handler() -> type:
    class _BlockingHandler:
        def __init__(self, *args, **kwargs):
            time.sleep(2.0)

        def log_message(self, *args, **kwargs):
            return

    return _BlockingHandler


@pytest.mark.timeout_seconds(10)
def test_slow_startup_is_served_from_backlog() -> None:
    """A child whose pre-serve stage is delayed still becomes reachable."""
    host, port, sock = _reserve_listening_socket()
    try:
        server_holder = []
        constructor_done = threading.Event()
        constructor_error = []

        def _construct():
            try:
                server_holder.append(
                    _FallbackHttpServer(
                        (host, port),
                        _make_blocking_handler(),
                        parent_socket=sock,
                    )
                )
            except Exception as exc:
                constructor_error.append(exc)
            finally:
                constructor_done.set()

        constructor = threading.Thread(target=_construct, daemon=True)
        constructor.start()

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(0.5)
            try:
                client.connect((host, port))
            except OSError as exc:
                if exc.errno in (socket.ECONNREFUSED,):
                    raise AssertionError(
                        f"inherited listening socket must not ECONNREFUSE"
                        f" a slow-starting child; got errno={exc.errno} {exc!r}"
                    ) from exc
        finally:
            client.close()

        assert constructor_done.wait(timeout=5.0)
        assert not constructor_error
        assert len(server_holder) == 1
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(1.0)
            client.connect((host, port))
        finally:
            client.close()
    finally:
        sock.close()
