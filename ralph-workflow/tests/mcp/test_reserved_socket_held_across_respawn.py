"""R1 in-scope regression: the parent-owned listening socket is held across respawns."""

from __future__ import annotations

import errno
import socket

import pytest

from ralph.mcp.server._reserved_socket import _reserve_listening_socket

pytestmark = pytest.mark.subprocess_e2e


def test_reserved_listening_socket_holds_port_until_closed() -> None:
    """Foreign ``bind`` raises ``EADDRINUSE`` while the socket is live."""
    host, port, sock = _reserve_listening_socket()
    try:
        assert host == "127.0.0.1"
        assert port > 0
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as excinfo:
                foreign.bind((host, port))
            assert excinfo.value.errno == errno.EADDRINUSE
        finally:
            foreign.close()
    finally:
        sock.close()

    foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        foreign.bind((host, port))
        foreign.close()
    except OSError as exc:  # pragma: no cover
        foreign.close()
        pytest.fail(f"expected a fresh bind to succeed after the parent closed: {exc!r}")


def test_reserved_listening_socket_is_listening() -> None:
    """The reserved socket is in LISTEN state (canonical kernel proof)."""
    _host, _port, sock = _reserve_listening_socket()
    try:
        accept_conn = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        assert accept_conn != 0
    finally:
        sock.close()
