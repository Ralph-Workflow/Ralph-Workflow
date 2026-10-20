"""R1 invariant: a request sent during a child's delayed pre-serve stage is served.

Drives a REAL MCP subprocess (via start_mcp_server with real LifecycleDeps)
whose pre-serve phase is held by a CHILD-SIDE gate: the child blocks inside
``_FallbackStandaloneServer.run()`` after the server object exists but BEFORE
the first accept(), until a sentinel file appears (env
``RALPH_MCP_TEST_PRE_SERVE_GATE``, injected through ``McpServerExtras.extra_env``).

While the child is gated, a client opens a TCP connection, sends a REAL HTTP
request, and keeps the socket open. The kernel queues the connection in the
listen backlog; the test proves nothing has accepted it yet (no bytes come
back within a bounded window), then releases the gate and asserts the SAME
connection receives a well-formed HTTP response. ECONNREFUSED is structurally
impossible because the bridge owns the listening socket from the start.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.env import MCP_TEST_PRE_SERVE_GATE_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_server_extras import McpServerExtras
from ralph.mcp.server._reserved_socket import _reserve_listening_socket
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps

pytestmark = pytest.mark.subprocess_e2e

_STAGE_FILE_GLOB = ".ralph_mcp_start_stage.*"


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-slow-startup-served-from-backlog",
        run_id="run-slow-startup-served-from-backlog",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


@dataclass
class _PortSignal:
    """Test-side synchronization for learning the reserved host/port."""

    port_ready: threading.Event = field(default_factory=threading.Event)
    reserved: list[tuple[str, int]] = field(default_factory=list)


def _make_deps(signal: _PortSignal) -> LifecycleDeps:
    """Wrap only ``reserve_listening_socket`` so the test learns the endpoint.

    The preflight and spawn deps stay REAL: the parent's own preflight request
    must queue in the backlog behind the child-side gate exactly like a
    production slow-startup client.
    """

    default_deps = lifecycle._default_lifecycle_deps()
    default_reserve = default_deps.reserve_listening_socket or _reserve_listening_socket

    def recording_reserve() -> tuple[str, int, socket.socket]:
        host, port, sock = default_reserve()
        signal.reserved.append((host, port))
        signal.port_ready.set()
        return host, port, sock

    return default_deps.__replace__(reserve_listening_socket=recording_reserve)


def _stage_files(workspace: Path) -> list[Path]:
    return [
        p
        for p in sorted((workspace / ".agent").glob(_STAGE_FILE_GLOB))
        if not p.name.endswith(".tmp")
    ]


def _http_request(host: str, port: int) -> bytes:
    return (
        b"POST /mcp HTTP/1.1\r\n"
        + f"Host: {host}:{port}\r\n".encode()
        + b"Content-Length: 0\r\n"
        + b"Connection: close\r\n\r\n"
    )


def _read_response_head(sock: socket.socket, timeout_s: float) -> str:
    response = b""
    sock.settimeout(timeout_s)
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    return response.split(b"\r\n\r\n", 1)[0].decode("latin-1")


def _read_last_stage_label(workspace: Path, deadline: float) -> str:
    while time.monotonic() < deadline:
        stages = _stage_files(workspace)
        if stages:
            with contextlib.suppress(OSError):
                label = stages[-1].read_text(encoding="utf-8").strip()
                if label:
                    return label
        time.sleep(0.002)
    return ""


@pytest.mark.timeout_seconds(60)
def test_slow_startup_is_served_from_backlog(tmp_path: Path) -> None:
    """A real HTTP request sent while the child is pre-accept must be answered on the same connection."""
    session = _make_session()
    workspace = FsWorkspace(tmp_path)
    sentinel = tmp_path / "pre_serve_gate"
    extras = McpServerExtras(extra_env={MCP_TEST_PRE_SERVE_GATE_ENV: str(sentinel)})

    signal = _PortSignal()
    deps = _make_deps(signal)

    result: list[lifecycle.RestartAwareMcpBridge | BaseException] = []
    done = threading.Event()

    def _start() -> None:
        try:
            result.append(lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras))
        except BaseException as exc:  # surfaced below
            result.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_start, daemon=True)
    worker.start()

    bridge: lifecycle.RestartAwareMcpBridge | None = None
    sock: socket.socket | None = None
    try:
        # Bounded wait for the bridge to own the listening socket.
        assert signal.port_ready.wait(timeout=15.0), "bridge never reserved its listening socket"
        host, port = signal.reserved[0]
        assert port > 0

        # Proof 1 (child-side, passive): the child's own stage file must exist
        # by now and must NOT yet say ready_to_serve — the child is held
        # before the serving boundary.
        stage_label = _read_last_stage_label(tmp_path, time.monotonic() + 15.0)
        assert stage_label, "child never wrote a startup stage file"
        assert stage_label != "ready_to_serve", (
            f"child reached {stage_label!r} before the gate was released"
        )

        # Proof 2 (child-side, active): a real request sent now gets NO bytes
        # back within a bounded window — nothing has called accept() yet. The
        # TCP connect itself succeeds because the parent-owned socket listens.
        sock = socket.create_connection((host, port), timeout=5.0)
        sock.sendall(_http_request(host, port))
        sock.settimeout(0.1)
        # socket.timeout is an alias of TimeoutError on Python 3.10+.
        with pytest.raises(TimeoutError):
            sock.recv(4096)

        # Release the child-side gate: the child crosses the serving boundary,
        # accepts from the kernel backlog, and answers BOTH the parent's real
        # preflight request and the retained client connection.
        sentinel.touch()
        assert done.wait(timeout=30.0), "start_mcp_server did not return after gate release"
        assert result, "start_mcp_server did not return"
        assert not isinstance(result[0], BaseException), repr(result[0])
        bridge = result[0]
        assert bridge.process.poll() is None, "child must be alive after startup"

        # The SAME connection must now receive a well-formed HTTP response.
        head = _read_response_head(sock, timeout_s=15.0)
        assert head.startswith("HTTP/1.1 "), (
            f"no HTTP response on the request-during-startup connection: {head!r}"
        )
        status = int(head.split(" ", 2)[1])
        assert status < 500, f"server error served on retained connection: {head!r}"
    finally:
        sentinel.touch()
        if sock is not None:
            sock.close()
        if bridge is not None:
            bridge.shutdown()
        worker.join(timeout=10.0)


@pytest.mark.timeout_seconds(60)
def test_slow_startup_preflight_succeeds(tmp_path: Path) -> None:
    """Preflight must succeed even when the child takes seconds to cross the serving boundary."""
    session = _make_session()
    workspace = FsWorkspace(tmp_path)
    sentinel = tmp_path / "pre_serve_gate"
    extras = McpServerExtras(extra_env={MCP_TEST_PRE_SERVE_GATE_ENV: str(sentinel)})

    signal = _PortSignal()
    deps = _make_deps(signal)

    # start_mcp_server runs the REAL preflight; it can only complete once the
    # child crosses the gate. Releasing right after port reservation bounds
    # the wait deterministically while keeping the real inherited-socket path.
    result: list[lifecycle.RestartAwareMcpBridge | BaseException] = []
    done = threading.Event()

    def _start() -> None:
        try:
            result.append(lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras))
        except BaseException as exc:  # surfaced below
            result.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_start, daemon=True)
    worker.start()
    assert signal.port_ready.wait(timeout=15.0), "bridge never reserved its listening socket"
    sentinel.touch()
    assert done.wait(timeout=30.0), "start_mcp_server did not return after gate release"
    assert result and not isinstance(result[0], BaseException), repr(result)
    bridge = result[0]

    try:
        assert bridge.process.poll() is None, "child must be alive after successful preflight"

        endpoint = bridge.endpoint_uri()
        host_colon_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
        host, port_str = host_colon_port.rsplit(":", 1)
        port = int(port_str)

        # The endpoint must serve HTTP immediately after start_mcp_server returns.
        sock = socket.create_connection((host, port), timeout=5.0)
        try:
            sock.sendall(_http_request(host, port))
            head = _read_response_head(sock, timeout_s=15.0)
        finally:
            sock.close()
        assert head.startswith("HTTP/1.1 "), (
            f"endpoint {endpoint} must serve HTTP after slow startup: {head!r}"
        )
    finally:
        bridge.shutdown()
        worker.join(timeout=10.0)
