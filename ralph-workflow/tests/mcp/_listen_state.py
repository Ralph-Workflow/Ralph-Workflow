import errno
import socket

import psutil


def socket_is_listening(sock: socket.socket) -> bool:
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
