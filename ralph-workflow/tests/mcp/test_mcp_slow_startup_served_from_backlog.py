"""R1 invariant: a child whose pre-serve stage is delayed still passes preflight.

Drives a REAL MCP subprocess (via start_mcp_server with real LifecycleDeps) whose
preflight succeeds — the parent waits for the child to become ready even though
the child is not immediately responsive.  A client that connects before the child
finishes startup is queued in the kernel's listen backlog and is served once the
child completes its init.  ECONNREFUSED is structurally impossible because the
bridge owns the listening socket from the start.
"""

from __future__ import annotations

import socket
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps

pytestmark = pytest.mark.subprocess_e2e


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-slow-startup-served-from-backlog",
        run_id="run-slow-startup-served-from-backlog",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


def _make_deps(
    tmp_path: Path,
    real_spawn_process: lifecycle.SpawnProcess,
    startup_delay_s: float,
) -> LifecycleDeps:
    """Build deps with a preflight that blocks for ``startup_delay_s`` seconds.

    This simulates a child that is slow to become responsive (e.g. heavy
    registry build, slow upstream probe) without depending on production hooks.
    The preflight is the PARENT's health check — blocking it does not affect
    the child's own startup path.
    """

    def slow_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        # Sleep to simulate the child being slow to respond.  The socket is
        # already in LISTEN state (kernel accepts the connection into the
        # backlog), but the child has not finished its init yet.
        time.sleep(startup_delay_s)

    default_deps = lifecycle._default_lifecycle_deps()
    return default_deps.__replace__(
        preflight=slow_preflight,
        spawn_process=real_spawn_process,
    )


def _connect_with_timeout(host: str, port: int, timeout_s: float) -> bool:
    """Return True if connect succeeds within timeout_s, False on timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _extract_port(endpoint: str) -> int:
    """Parse the port number from an endpoint URL such as ``http://127.0.0.1:43199/mcp``."""
    host_colon_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
    return int(host_colon_port.rsplit(":", 1)[1])


@pytest.mark.timeout_seconds(25)
def test_slow_startup_is_served_from_backlog(tmp_path: Path) -> None:
    """A client connecting before the child is ready must be served once the child starts.

    The bridge holds the listening socket; the kernel accepts the connection into
    the backlog immediately on connect().  While preflight blocks waiting for the
    child to become ready, the connection is already queued.  Once the child's
    preflight unblocks, the HTTP server is fully up and the queued connection
    is served.
    """
    startup_delay = 3.0  # seconds — preflight blocks this long
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    deps = _make_deps(tmp_path, real_spawn, startup_delay_s=startup_delay)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

    try:
        endpoint = bridge.endpoint_uri()
        port = _extract_port(endpoint)

        # The child is alive but preflight is blocked (child still starting).
        # A connect() against the bridge-owned socket must NOT ECONNREFUSED —
        # the kernel accepts the connection into the backlog immediately.
        early_conn_ok = _connect_with_timeout("127.0.0.1", port, timeout_s=2.0)
        assert early_conn_ok, (
            "connect() during child's slow startup must succeed (kernel queues it); "
            "ECONNREFUSED is impossible on a bridge-owned inherited socket"
        )

        # The child is still starting (preflight blocking).  Verify the socket
        # stays in LISTEN state and foreign bind still fails.
        listening_socket = bridge._listening_socket
        assert listening_socket is not None
        backlog_state = listening_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        assert backlog_state != 0, "SO_ACCEPTCONN must stay non-zero during slow startup"

        # After preflight completes (child is ready), verify the endpoint is up
        deadline = time.monotonic() + 5.0
        child_exited = False
        while time.monotonic() < deadline:
            if bridge.process.poll() is not None:
                child_exited = True
                break
            time.sleep(0.2)

        assert not child_exited, "child must still be running after slow preflight completes"
        assert bridge.process.poll() is None, "child must be alive after preflight"

        # Endpoint must answer HTTP after preflight completes
        http_ok = _connect_with_timeout("127.0.0.1", port, timeout_s=2.0)
        assert http_ok, f"endpoint {endpoint} must answer after child startup"

    finally:
        bridge.shutdown()


@pytest.mark.timeout_seconds(20)
def test_slow_startup_preflight_succeeds(tmp_path: Path) -> None:
    """Preflight must succeed even when the child takes several seconds to respond."""
    startup_delay = 4.0  # well within the default 30s preflight budget
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    deps = _make_deps(tmp_path, real_spawn, startup_delay_s=startup_delay)

    # start_mcp_server itself blocks until preflight succeeds or times out.
    # With startup_delay=4s and the default 30s preflight timeout, this must not
    # raise.
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

    try:
        assert bridge.process.poll() is None, "child must be alive after successful preflight"

        endpoint = bridge.endpoint_uri()
        port = _extract_port(endpoint)

        # The endpoint must be serving HTTP immediately after start_mcp_server returns
        http_ok = _connect_with_timeout("127.0.0.1", port, timeout_s=2.0)
        assert http_ok, f"endpoint {endpoint} must serve HTTP after slow startup"
    finally:
        bridge.shutdown()
