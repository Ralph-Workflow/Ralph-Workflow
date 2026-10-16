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
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._lifecycle_deps import LifecycleDeps, PreflightFn

if TYPE_CHECKING:
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


def _poll_endpoint(endpoint: str, deadline: float) -> bool:
    """Return True when ``endpoint`` answers a TCP connection before ``deadline``."""
    port = _extract_port(endpoint)
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


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
    workspace = lifecycle.FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    deps = _make_deps(tmp_path, real_spawn)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

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
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as exc_info:
                foreign.bind(( "127.0.0.1", listening_socket.getsockname()[1]))
            assert exc_info.value.errno == errno.EADDRINUSE, (
                f"foreign bind while bridge owns socket must EADDRINUSE, got {exc_info.value.errno}"
            )
        finally:
            foreign.close()

        # --- 5. ECONNREFUSED never occurs during the respawn window -------------
        endpoint = bridge.endpoint_uri()
        port = _extract_port(endpoint)
        conn_refused_seen = False
        connect_attempts = 0

        def try_connect() -> bool:
            """Return True if connect succeeds (child ready or backlogged), False on ECONNREFUSED."""
            nonlocal conn_refused_seen, connect_attempts
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                connect_attempts += 1
                sock.connect(("127.0.0.1", port))
                return True  # queued by kernel or already serving
            except OSError as exc:
                if exc.errno == errno.ECONNREFUSED:
                    conn_refused_seen = True
                    return False
                raise  # other error
            finally:
                sock.close()

        # Immediately after kill: connection must NOT be refused.
        # The kernel queues the connection because the socket stays in LISTEN state.
        conn_succeeded = try_connect()
        assert conn_succeeded, (
            "connect() after kill must succeed (kernel queues it); "
            "ECONNREFUSED is structurally impossible on a bridge-owned inherited socket"
        )
        assert not conn_refused_seen, (
            "ECONNREFUSED must never occur on a bridge-owned inherited socket"
        )

        # --- 6. trigger real respawn ------------------------------------------
        # check_health_and_restart_if_needed() restarts synchronously:
        #   - detects process_exited=True
        #   - increments restart_count
        #   - calls _restart_fn() → _spawn_mcp_process() with preflight
        #   - preflight waits for new child to respond
        #   - returns True
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True, "check_health_and_restart_if_needed must restart a dead child"
        assert bridge.restart_count == 1, "restart_count must be 1 after first respawn"

        # --- 7. endpoint serves real HTTP after respawn ------------------------
        deadline = time.monotonic() + 10.0
        http_ok = _poll_endpoint(endpoint, deadline)
        assert http_ok, (
            f"endpoint {endpoint} must answer after respawn; "
            "child may still be starting — increase preflight timeout if this is a flake"
        )

        # --- 8. child is alive again -------------------------------------------
        assert bridge.process.poll() is None, "child must be alive after successful respawn"

        # --- 9. final ECONNREFUSED check ----------------------------------------
        # After respawn: ECONNREFUSED must still be impossible
        conn_refused_seen = False
        conn_succeeded = try_connect()
        assert conn_succeeded, (
            "connect after respawn must succeed; ECONNREFUSED must be impossible"
        )
        assert not conn_refused_seen

    finally:
        bridge.shutdown()


@pytest.mark.timeout_seconds(15)
def test_inherited_socket_stays_listening_across_kill(tmp_path: Path) -> None:
    """After SIGKILL the socket stays open and in LISTEN state until bridge shutdown."""
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)

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
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as exc_info:
                foreign.bind(("127.0.0.1", listening_socket.getsockname()[1]))
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
