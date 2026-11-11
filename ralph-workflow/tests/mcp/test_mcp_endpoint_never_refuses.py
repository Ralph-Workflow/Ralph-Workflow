from __future__ import annotations

import errno
import socket
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.workspace.fs import FsWorkspace
from tests.mcp._listen_state import socket_is_listening

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps
    from ralph.mcp.server._process_like import ProcessLike

pytestmark = pytest.mark.subprocess_e2e


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-endpoint-never-refuses",
        run_id="run-endpoint-never-refuses",
        drain="development",
        capabilities={"WorkspaceRead", "WorkspaceEdit"},
    )


def _make_deps(
    tmp_path: Path,
    real_spawn_process: lifecycle.SpawnProcess,
) -> LifecycleDeps:

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    default_deps = lifecycle._default_lifecycle_deps()
    return default_deps.__replace__(preflight=noop_preflight, spawn_process=real_spawn_process)


def _extract_port(endpoint: str) -> int:
    host_colon_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
    return int(host_colon_port.rsplit(":", 1)[1])


def _wait_until_child_exits(process: ProcessLike, deadline: float) -> int | None:
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            return code
        time.sleep(0.05)
    return process.poll()


def _repeated_connects_during_respawn(
    bridge: lifecycle.RestartAwareMcpBridge, endpoint: str, *, deadline: float
) -> None:
    import threading

    restart_done = threading.Event()
    connect_errors: list[OSError] = []
    connect_lock = threading.Lock()

    def _restart_in_background() -> None:
        bridge.check_health_and_restart_if_needed()
        restart_done.set()

    restart_thread = threading.Thread(target=_restart_in_background, daemon=True)
    restart_thread.start()

    spin_deadline = time.monotonic() + 2.0
    while (
        bridge._inner.process.poll() is None
        and not restart_done.is_set()
        and time.monotonic() < spin_deadline
    ):
        time.sleep(0.01)

    connect_attempts = 0
    while not restart_done.is_set() and time.monotonic() < deadline:
        connect_attempts += 1
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.1)
        try:
            sock.connect(("127.0.0.1", _extract_port(endpoint)))
        except OSError as exc:
            with connect_lock:
                connect_errors.append(exc)
        finally:
            sock.close()
        time.sleep(0.01)

    restart_thread.join(timeout=5.0)

    with connect_lock:
        for exc in connect_errors:
            if exc.errno == errno.ECONNREFUSED:
                pytest.fail(
                    f"ECONNREFUSED during respawn window is impossible per R1 invariant; "
                    f"got {connect_attempts} connect attempt(s) with {len(connect_errors)} error(s): {connect_errors}"
                )


def _queued_connection_served_after_respawn(
    bridge: lifecycle.RestartAwareMcpBridge, endpoint: str
) -> str:
    port = _extract_port(endpoint)
    queued_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    queued_sock.settimeout(5.0)
    try:
        queued_sock.connect(("127.0.0.1", port))

        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True, "check_health_and_restart_if_needed must restart a dead child"
        assert bridge.restart_count >= 1, "restart_count must be >= 1 after a respawn"

        request = b"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        queued_sock.sendall(request)
        queued_sock.settimeout(0.2)
        response = b""
        deadline = time.monotonic() + 10.0
        while b"\r\n" not in response and time.monotonic() < deadline:
            try:
                chunk = queued_sock.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                break
            response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode(errors="replace")
        assert status_line.startswith("HTTP/1."), (
            f"the socket queued before respawn must be served by the respawned "
            f"child; got {response!r} on {endpoint}"
        )
    finally:
        queued_sock.close()
    return status_line


@pytest.mark.timeout_seconds(35)
def test_endpoint_never_refuses_during_real_respawn(tmp_path: Path) -> None:
    session = _make_session()
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    deps = _make_deps(tmp_path, real_spawn)

    extras = lifecycle.McpServerExtras(
        restart_policy=lifecycle.McpRestartPolicy(cooldown_seconds=0.0),
    )
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        assert bridge.process.poll() is None, "child must be alive immediately after start"

        bridge.process.kill()
        exit_code = _wait_until_child_exits(bridge.process, deadline=time.monotonic() + 5.0)
        assert exit_code is not None, "kill() should make poll() return an exit code"

        listening_socket = bridge._listening_socket
        assert listening_socket is not None, "bridge must hold _listening_socket"
        assert listening_socket.fileno() >= 0, "socket fd must be valid"
        assert socket_is_listening(listening_socket), (
            "inherited socket must stay in LISTEN state "
            "while the child is dead; ECONNREFUSED is structurally impossible"
        )

        sock_addr = listening_socket.getsockname()
        assert isinstance(sock_addr, tuple)
        bind_port = int(sock_addr[1])
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as exc_info:
                foreign.bind(("127.0.0.1", bind_port))
            assert exc_info.value.errno == errno.EADDRINUSE, (
                f"foreign bind while bridge owns socket must EADDRINUSE, got {exc_info.value.errno}"
            )
        finally:
            foreign.close()

        endpoint = bridge.endpoint_uri()
        connect_deadline = time.monotonic() + 8.0
        _repeated_connects_during_respawn(bridge, endpoint, deadline=connect_deadline)

        bridge._inner.process.kill()
        _wait_until_child_exits(bridge._inner.process, deadline=time.monotonic() + 3.0)
        _queued_connection_served_after_respawn(bridge, endpoint)

        assert bridge.process.poll() is None, "child must be alive after successful respawn"

        port = _extract_port(endpoint)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno == errno.ECONNREFUSED:
                pytest.fail("ECONNREFUSED after respawn must be impossible")
        finally:
            probe.close()

    finally:
        bridge.shutdown()


@pytest.mark.timeout_seconds(15)
def test_inherited_socket_stays_listening_across_kill(tmp_path: Path) -> None:
    session = _make_session()
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    deps = _make_deps(tmp_path, real_spawn)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

    try:
        assert bridge.process.poll() is None

        bridge.process.kill()
        _wait_until_child_exits(bridge.process, deadline=time.monotonic() + 3.0)

        listening_socket = bridge._listening_socket
        assert listening_socket is not None
        assert socket_is_listening(listening_socket), (
            "socket must stay in LISTEN state while bridge owns the socket"
        )

        sock_addr = listening_socket.getsockname()
        assert isinstance(sock_addr, tuple)
        bind_port = int(sock_addr[1])
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as exc_info:
                foreign.bind(("127.0.0.1", bind_port))
            assert exc_info.value.errno == errno.EADDRINUSE
        finally:
            foreign.close()

        port = _extract_port(bridge.endpoint_uri())
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno == errno.ECONNREFUSED:
                pytest.fail("ECONNREFUSED on a bridge-owned socket must not happen")
        finally:
            sock.close()
    finally:
        bridge.shutdown()
