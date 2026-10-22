"""Portable LISTEN-state probe for sockets owned by the test process."""

import errno
import socket

import psutil


def socket_is_listening(sock: socket.socket) -> bool:
    """Return whether ``sock`` is in the kernel's LISTEN state.

    Linux answers through ``SO_ACCEPTCONN``. macOS rejects that option with
    ``ENOPROTOOPT``, so fall back to this process's own TCP table, which is
    just as passive: neither probe queues a connection on the backlog.
    """
    try:
        return sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 0
    except OSError as exc:
        if exc.errno != errno.ENOPROTOOPT:
            raise
    address = sock.getsockname()
    return any(
        conn.laddr == address and conn.status == psutil.CONN_LISTEN
        for conn in psutil.Process().net_connections(kind="tcp")
    )
