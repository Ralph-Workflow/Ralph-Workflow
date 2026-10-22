"""R1 in-scope regression: the parent-owned listening socket is held across respawns."""

from __future__ import annotations

import errno
import pathlib
import socket
from datetime import timedelta

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._reserved_socket import _reserve_listening_socket
from ralph.workspace.fs import FsWorkspace
from tests.mcp._listen_state import socket_is_listening

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
        assert socket_is_listening(sock)
    finally:
        sock.close()


class _FakeProcess:
    def __init__(self, pid: int = 9999) -> None:
        self._pid = pid

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_parent_holds_listening_socket_from_start_until_shutdown(tmp_path: pathlib.Path) -> None:
    """Parent holds listening socket from start_mcp_server until shutdown()."""
    session = AgentSession(
        session_id="test-session",
        run_id="run-1",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    def fake_spawn(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        return _FakeProcess()

    deps = lifecycle.LifecycleDeps(
        reserve_listening_socket=_reserve_listening_socket,
        create_session_file=lambda root, s: session_file,
        subprocess_env=lambda *args, **kwargs: {},
        spawn_process=fake_spawn,
        preflight=lambda ep, tools, to: None,
        preflight_timeout=lambda: timedelta(seconds=5),
        probe=lambda ep, to: None,
        probe_timeout=lambda: timedelta(seconds=5),
    )

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)
    host: str = "127.0.0.1"
    port: int = 0
    try:
        listening_socket = bridge._listening_socket
        assert listening_socket is not None
        assert listening_socket.fileno() >= 0

        # In LISTEN state
        assert socket_is_listening(listening_socket)

        # Foreign bind raises EADDRINUSE
        sock_addr = listening_socket.getsockname()
        assert isinstance(sock_addr, tuple)
        host = str(sock_addr[0])
        port = int(sock_addr[1])
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as excinfo:
                foreign.bind((host, port))
            assert excinfo.value.errno == errno.EADDRINUSE
        finally:
            foreign.close()
    finally:
        bridge.shutdown()

    # After shutdown, socket is closed and released
    assert bridge._listening_socket is None
    foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        foreign.bind((host, port))
    finally:
        foreign.close()
