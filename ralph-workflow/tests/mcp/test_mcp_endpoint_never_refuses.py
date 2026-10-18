"""R1 root-cause invariant: the endpoint never returns ``ECONNREFUSED`` to a queued client.

Drives the REAL production respawn path (RestartAwareMcpBridge._restart_fn via
check_health_and_restart_if_needed), with a real child subprocess that adopts the
parent-owned inherited listening socket.  After SIGKILL-ing the child, the
kernel backlog absorbs new connections while the bridge restarts the child;
``ECONNREFUSED`` is structurally impossible because the socket stays in
LISTEN state under the bridge's ownership throughout.
"""

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

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps
    from ralph.mcp.server._process_like import ProcessLike

pytestmark = pytest.mark.subprocess_e2e


def _make_session() -> AgentSession:
    """Build a minimal AgentSession covering the fields lifecycle reads."""
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
    """Build LifecycleDeps with a no-op preflight so the bridge starts fast.

    The no-op preflight skips the real HTTP probe; the test manually verifies
    endpoint accessibility after each respawn.  This keeps test wall time
    bounded while still exercising the real child-spawn, inherited-FD handoff,
    and respawn machinery.
    """

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    default_deps = lifecycle._default_lifecycle_deps()
    # LifecycleDeps is frozen; use __replace__ to override preflight only.
    return default_deps.__replace__(preflight=noop_preflight, spawn_process=real_spawn_process)


def _extract_port(endpoint: str) -> int:
    """Parse the port number from an endpoint URL such as ``http://127.0.0.1:43199/mcp``."""
    # 'http://127.0.0.1:43199/mcp' -> '127.0.0.1:43199' -> '43199'
    host_colon_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
    return int(host_colon_port.rsplit(":", 1)[1])


def _wait_until_child_exits(process: ProcessLike, deadline: float) -> int | None:
    """Block until the process exits or the deadline passes; return the exit code."""
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            return code
        time.sleep(0.05)
    return process.poll()


def _repeated_connects_during_respawn(
    bridge: lifecycle.RestartAwareMcpBridge, endpoint: str, *, deadline: float
) -> None:
    """Repeatedly attempt TCP connections during the respawn window.

    Fires multiple connect attempts while check_health_and_restart_if_needed()
    is blocked waiting for the new child to become ready. Each attempt must
    NOT raise ECONNREFUSED — the kernel backlog absorbs connections because
    the bridge-owned listening socket stays open throughout the respawn.

    The R1 invariant is structurally absolute: ECONNREFUSED is impossible
    whenever the bridge owns the listening socket, regardless of whether the
    current child is alive, dead, or in-flight to respawn.
    """
    import threading

    restart_done = threading.Event()
    connect_errors: list[OSError] = []
    connect_lock = threading.Lock()

    def _restart_in_background() -> None:
        bridge.check_health_and_restart_if_needed()
        restart_done.set()

    restart_thread = threading.Thread(target=_restart_in_background, daemon=True)
    restart_thread.start()

    # Spin until the restart actually kicks in. After SIGKILL the process
    # is already dead (poll() != None), so we just wait for the background
    # thread's health check to notice and call _restart_fn.
    spin_deadline = time.monotonic() + 2.0
    while (
        bridge._inner.process.poll() is None
        and not restart_done.is_set()
        and time.monotonic() < spin_deadline
    ):
        time.sleep(0.001)

    # Repeated connect attempts during the respawn window.
    # Short timeout (0.1 s) so we sample frequently; the connect itself may
    # succeed (backlog) or fail non-ECONNREFUSED (e.g. timeout waiting for
    # accept), but ECONNREFUSED is the structural impossibility we must catch.
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

    # ECONNREFUSED is the structural impossibility — any other error is expected.
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
    """Open a socket while the child is dead, respawn, and assert the SAME socket
    receives a valid HTTP response from the respawned child (which inherits the
    bridge listening socket). Returns the received status line."""
    # A REAL connection is established now (child is dead; the kernel
    # backlog absorbs it because the bridge-owned socket stays LISTEN) and
    # is KEPT OPEN across the respawn.  After the respawned child inherits
    # the listening socket and accepts, this SAME socket must receive a
    # valid HTTP response — that is the queued-connection-served invariant.
    port = _extract_port(endpoint)
    queued_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    queued_sock.settimeout(5.0)
    try:
        queued_sock.connect(("127.0.0.1", port))  # must not raise ECONNREFUSED

        # check_health_and_restart_if_needed() restarts synchronously while
        # ``queued_sock`` is still open and un-accepted by any child.
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True, "check_health_and_restart_if_needed must restart a dead child"
        assert bridge.restart_count >= 1, "restart_count must be >= 1 after a respawn"

        # SAME socket receives a valid HTTP response.
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
    """ECONNREFUSED is impossible during a real bridge respawn because the
    bridge owns the listening socket for its full lifetime.

    Sequence:
      1. start_mcp_server → real child adopts the inherited FD socket.
      2. SIGKILL the child; the socket stays owned by the bridge.
      3. During the brief respawn window the kernel queues connects.
      4. check_health_and_restart_if_needed() swaps to a new child.
      5. After the new child is ready, the endpoint serves real HTTP.
    """
    session = _make_session()
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    deps = _make_deps(tmp_path, real_spawn)

    extras = lifecycle.McpServerExtras(
        restart_policy=lifecycle.McpRestartPolicy(cooldown_seconds=0.0),
    )
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        # --- 1. child is alive -------------------------------------------------
        assert bridge.process.poll() is None, "child must be alive immediately after start"

        # --- 2. kill child with SIGKILL ----------------------------------------
        bridge.process.kill()
        exit_code = _wait_until_child_exits(bridge.process, deadline=time.monotonic() + 5.0)
        assert exit_code is not None, "kill() should make poll() return an exit code"

        # --- 3. R1 invariant: socket stays in LISTEN state while child is dead -
        listening_socket = bridge._listening_socket
        assert listening_socket is not None, "bridge must hold _listening_socket"
        assert listening_socket.fileno() >= 0, "socket fd must be valid"
        backlog_state = listening_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        assert backlog_state != 0, (
            "inherited socket must stay in LISTEN state (SO_ACCEPTCONN != 0) "
            "while the child is dead; ECONNREFUSED is structurally impossible"
        )

        # --- 4. R1 invariant: foreign bind raises EADDRINUSE --------------------
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

        # --- 5. R1 invariant: REPEATED connect attempts during the respawn window
        # never raise ECONNREFUSED (kernel backlog absorbs each new connection).
        # Run the respawn in a background thread and hammer the endpoint from
        # the main thread so every connect lands while the child is in-flight.
        endpoint = bridge.endpoint_uri()
        connect_deadline = time.monotonic() + 8.0
        _repeated_connects_during_respawn(bridge, endpoint, deadline=connect_deadline)

        # --- 6. Queued connection served after respawn --------------------------
        # The repeated-connect loop already performed one full restart cycle
        # (restart_count is now 1). _queued_connection_served_after_respawn
        # will find the child already alive and the restart_count already
        # incremented — skip its own restart path but still verify the
        # queued-connection-served invariant (a second health-check on an
        # already-healthy child is a no-op; the queued socket from step 3
        # is still open and will be served if we trigger one more restart).
        bridge._inner.process.kill()
        _wait_until_child_exits(bridge._inner.process, deadline=time.monotonic() + 3.0)
        _queued_connection_served_after_respawn(bridge, endpoint)

        # --- 8. child is alive again -------------------------------------------
        assert bridge.process.poll() is None, "child must be alive after successful respawn"

        # --- 9. final ECONNREFUSED check ----------------------------------------
        # After respawn: ECONNREFUSED must still be impossible
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
    """After SIGKILL the socket stays open and in LISTEN state until bridge shutdown."""
    session = _make_session()
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    deps = _make_deps(tmp_path, real_spawn)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

    try:
        assert bridge.process.poll() is None

        # kill without restarting
        bridge.process.kill()
        _wait_until_child_exits(bridge.process, deadline=time.monotonic() + 3.0)

        # socket is still live and in LISTEN state
        listening_socket = bridge._listening_socket
        assert listening_socket is not None
        backlog_state = listening_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        assert backlog_state != 0, "SO_ACCEPTCONN must be non-zero while bridge owns the socket"

        # foreign bind still fails
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

        # a connect still queues (no ECONNREFUSED)
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
