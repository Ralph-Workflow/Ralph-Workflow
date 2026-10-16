"""R1 invariant: a request sent during a child's delayed pre-serve stage is served.

Drives a REAL MCP subprocess (via start_mcp_server with real LifecycleDeps).
While the parent-side preflight is still blocked (the pre-serve child has not
been released), a client opens a TCP connection, sends a REAL HTTP request, and
keeps the socket open. The kernel queues the connection in the listen backlog;
once the child is released and its HTTP server accepts, the SAME connection
must receive a well-formed HTTP response. ECONNREFUSED is structurally
impossible because the bridge owns the listening socket from the start.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
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


@dataclass
class _StartupGate:
    """Test-side synchronization for the slow-startup scenario."""

    # Set when the bridge owns the listening socket (port known, pre-serve
    # child NOT yet released).
    port_ready: threading.Event = field(default_factory=threading.Event)
    # Test sets this to release the blocked preflight (= child ready).
    release: threading.Event = field(default_factory=threading.Event)
    reserved: list[tuple[str, int]] = field(default_factory=list)


def _make_deps(
    real_spawn_process: lifecycle.SpawnProcess,
    gate: _StartupGate,
) -> LifecycleDeps:
    """Build deps with a preflight that blocks until ``gate.release`` is set.

    This keeps ``start_mcp_server`` from returning while the test sends its
    request, simulating a child still mid-startup (heavy registry build, slow
    upstream probe). The wrapping ``reserve_listening_socket`` records the
    reserved host/port and sets ``gate.port_ready`` the moment the bridge owns
    the listening socket — the deterministic seam proving the request happens
    BEFORE child readiness.
    """

    def slow_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        gate.release.wait(timeout=timeout.total_seconds())

    default_deps = lifecycle._default_lifecycle_deps()
    default_reserve = default_deps.reserve_listening_socket or lifecycle._reserve_listening_socket

    def recording_reserve() -> tuple[str, int, socket.socket]:
        host, port, sock = default_reserve()
        gate.reserved.append((host, port))
        gate.port_ready.set()
        return host, port, sock

    return default_deps.__replace__(
        preflight=slow_preflight,
        spawn_process=real_spawn_process,
        reserve_listening_socket=recording_reserve,
    )


@pytest.mark.timeout_seconds(30)
def test_slow_startup_is_served_from_backlog(tmp_path: Path) -> None:
    """A real HTTP request sent mid-startup must be answered on the same connection."""
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    gate = _StartupGate()
    deps = _make_deps(real_spawn, gate)

    # Run start_mcp_server on a thread so the test can send its request while
    # preflight is still blocked (pre-serve child not yet released).
    result: list[lifecycle.RestartAwareMcpBridge | BaseException] = []
    done = threading.Event()

    def _start() -> None:
        try:
            result.append(lifecycle.start_mcp_server(session, workspace, deps=deps))
        except BaseException as exc:  # surfaced below
            result.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_start, daemon=True)
    worker.start()

    bridge: lifecycle.RestartAwareMcpBridge | None = None
    try:
        # Bounded wait for the bridge to own the listening socket — no sleeps,
        # no polling loops longer than one wait call.
        assert gate.port_ready.wait(timeout=15.0), (
            "bridge never reserved its listening socket"
        )
        host, port = gate.reserved[0]
        assert port > 0

        # The preflight is STILL blocked here: the child has not been released.
        assert not gate.release.is_set()
        assert worker.is_alive(), (
            "start_mcp_server must still be blocked in preflight (child not ready)"
        )
        request_sent_before_readiness = True

        # Send a REAL HTTP request now and RETAIN the connection.
        request = (
            b"POST /mcp HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode()
            + b"Content-Length: 0\r\n"
            + b"Connection: close\r\n\r\n"
        )
        sock = socket.create_connection((host, port), timeout=5.0)
        sock.sendall(request)

        # Release the preflight: the child finishes startup and its HTTP
        # server starts accepting. The kernel hands over the queued
        # connection from the listen backlog.
        gate.release.set()
        assert done.wait(timeout=10.0), "start_mcp_server did not return after release"
        assert result, "start_mcp_server did not return"
        assert not isinstance(result[0], BaseException), repr(result[0])
        bridge = result[0]
        assert bridge.process.poll() is None, "child must be alive after startup"

        # The SAME connection must receive a well-formed HTTP response.
        response = b""
        sock.settimeout(10.0)
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        head = response.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        assert head.startswith("HTTP/1.1 "), (
            f"no HTTP response on the request-during-startup connection: {head!r}"
        )
        status = int(head.split(" ", 2)[1])
        assert status < 500, f"server error served on retained connection: {head!r}"
        assert request_sent_before_readiness
    finally:
        gate.release.set()
        if bridge is not None:
            bridge.shutdown()
        worker.join(timeout=10.0)


@pytest.mark.timeout_seconds(40)
def test_slow_startup_preflight_succeeds(tmp_path: Path) -> None:
    """Preflight must succeed even when the child takes several seconds to respond."""
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    gate = _StartupGate()
    deps = _make_deps(real_spawn, gate)

    # start_mcp_server itself blocks until preflight succeeds or times out.
    # Releasing after the port is reserved keeps the real inherited-socket
    # path while bounding the wait deterministically. Run it on a thread so
    # the test can observe port reservation before releasing the preflight.
    result: list[lifecycle.RestartAwareMcpBridge | BaseException] = []
    done = threading.Event()

    def _start() -> None:
        try:
            result.append(lifecycle.start_mcp_server(session, workspace, deps=deps))
        except BaseException as exc:  # surfaced below
            result.append(exc)
        finally:
            done.set()

    threading.Thread(target=_start, daemon=True).start()
    assert gate.port_ready.wait(timeout=15.0), "bridge never reserved its listening socket"
    gate.release.set()
    assert done.wait(timeout=10.0), "start_mcp_server did not return after release"
    assert result and not isinstance(result[0], BaseException), repr(result)
    bridge = result[0]

    try:
        assert bridge.process.poll() is None, "child must be alive after successful preflight"

        endpoint = bridge.endpoint_uri()
        host_colon_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
        port = int(host_colon_port.rsplit(":", 1)[1])

        # The endpoint must serve HTTP immediately after start_mcp_server returns
        sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        sock.sendall(
            b"POST /mcp HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{port}\r\n".encode()
            + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        head = b""
        sock.settimeout(10.0)
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                break
            head += chunk
        assert head.startswith(b"HTTP/1.1 "), (
            f"endpoint {endpoint} must serve HTTP after slow startup: {head!r}"
        )
    finally:
        bridge.shutdown()
