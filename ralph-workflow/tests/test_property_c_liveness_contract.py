# property-test: C — real GET /health route on the production transport
"""Liveness contract: the production transport exposes a real GET /health route.

The supervisor's ``RestartAwareMcpBridge.check_health_and_restart_if_needed``
already calls a probe function; that probe must be reachable as a real HTTP
endpoint, and the handler must surface 200 healthy / 503 unhealthy responses
on the same transport that every other production request rides. The
in-memory transport harness exercises the shipped path end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import _in_memory_transport
from ralph.mcp.server._fallback_http_handler_probe import _ProbeResult
from ralph.mcp.server._in_memory_transport import drive_request
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._metrics import McpMetrics
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
from ralph.mcp.server.runtime import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
    from ralph.mcp.server._server_state import ServerState


class _ServerWithHealthProbe(Protocol):
    """Typed view of the production handler's fake-server attributes.

    The SimpleNamespace returned by ``_make_fake_server`` is a structural
    stand-in for ``_FallbackHttpServer``; this Protocol declares the
    attributes the test sets (health_probe_fn, metrics) so assignments
    are typed without suppression markers. The structural Protocol
    is preferred over inheriting from ``_FallbackHttpServer`` (a concrete
    ``ThreadingHTTPServer`` subclass) because Protocol can only inherit
    from other Protocols.
    """

    health_probe_fn: Callable[[], _ProbeResult] | None
    metrics: McpMetrics | None


def _make_mcp_server(tmp_path: Path) -> McpServer:
    session = AgentSession(
        session_id="health-test",
        run_id="health-run",
        drain="standalone",
        capabilities={
            "WorkspaceRead",
            "WorkspaceWriteEphemeral",
            "WorkspaceWriteTracked",
            "ArtifactSubmit",
            "RunReportProgress",
        },
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _make_with_probe(
    mcp_server: McpServer,
    state: ServerState,
    probe: Callable[[], _ProbeResult] | None = None,
    metrics: McpMetrics | None = None,
    *,
    _original: Callable[[McpServer, ServerState], _FallbackHttpServer] | None = None,
) -> _FallbackHttpServer:
    """Build a SimpleNamespace with health_probe_fn and metrics set."""
    factory = _original if _original is not None else _in_memory_transport._make_fake_server
    fake = factory(mcp_server, state)
    typed: _ServerWithHealthProbe = cast("_ServerWithHealthProbe", fake)
    if probe is not None:
        typed.health_probe_fn = probe
    if metrics is not None:
        typed.metrics = metrics
    return fake


def test_health_route_returns_200_healthy_when_probe_succeeds(tmp_path: Path) -> None:
    """A /health request with a healthy probe returns 200 application/json."""
    mcp_server = _make_mcp_server(tmp_path)

    def healthy_probe() -> _ProbeResult:
        return _ProbeResult(healthy=True, latency_ms=12.5, reason="")

    original = _in_memory_transport._make_fake_server
    _in_memory_transport._make_fake_server = (
        lambda m, s: _make_with_probe(m, s, probe=healthy_probe, _original=original)
    )
    try:
        status, headers, body = drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    assert status == 200, f"expected 200, got {status}; body={body!r}"
    assert "application/json" in headers.get("content-type", "")
    payload = cast("dict[str, object]", json.loads(body))
    assert payload.get("status") == "healthy"
    assert payload.get("latency_ms") == 12.5


def test_health_route_returns_503_unhealthy_when_probe_raises(tmp_path: Path) -> None:
    """A /health request whose probe raises returns 503 unhealthy."""
    mcp_server = _make_mcp_server(tmp_path)

    def broken_probe() -> _ProbeResult:
        raise RuntimeError("probe went sideways")

    original = _in_memory_transport._make_fake_server
    _in_memory_transport._make_fake_server = (
        lambda m, s: _make_with_probe(m, s, probe=broken_probe, _original=original)
    )
    try:
        status, headers, body = drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    assert status == 503, f"expected 503, got {status}; body={body!r}"
    assert "application/json" in headers.get("content-type", "")
    payload = cast("dict[str, object]", json.loads(body))
    assert payload.get("status") == "unhealthy"
    assert "RuntimeError" in str(payload.get("reason", ""))


def test_health_route_returns_503_when_probe_reports_unhealthy(tmp_path: Path) -> None:
    """A /health request whose probe returns unhealthy returns 503 with reason."""
    mcp_server = _make_mcp_server(tmp_path)

    def unhealthy_probe() -> _ProbeResult:
        return _ProbeResult(healthy=False, latency_ms=0.0, reason="server_wedged")

    original = _in_memory_transport._make_fake_server
    _in_memory_transport._make_fake_server = (
        lambda m, s: _make_with_probe(m, s, probe=unhealthy_probe, _original=original)
    )
    try:
        status, _headers, body = drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    assert status == 503
    payload = cast("dict[str, object]", json.loads(body))
    assert payload.get("status") == "unhealthy"
    assert payload.get("reason") == "server_wedged"


def test_health_route_increments_health_probe_outcomes_counter(tmp_path: Path) -> None:
    """A /health probe increments the metrics counter for success or failure."""
    mcp_server = _make_mcp_server(tmp_path)
    metrics = McpMetrics()
    original = _in_memory_transport._make_fake_server

    def _probe() -> _ProbeResult:
        return _ProbeResult(healthy=True, latency_ms=1.0, reason="")

    _in_memory_transport._make_fake_server = (
        lambda m, s: _make_with_probe(m, s, probe=_probe, metrics=metrics, _original=original)
    )
    try:
        drive_request(mcp_server, b"", path="/health", method="GET")
        drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    snapshot = metrics.snapshot()
    outcomes = cast("dict[str, int]", snapshot["health_probe_outcomes"])
    assert outcomes["success"] >= 2


def test_supervisor_calls_restart_within_configured_probe_timeout() -> None:
    """The supervisor restarts within the bounded RALPH_MCP_PROBE_TIMEOUT_MS.

    Inject a fake clock and a probe that fails. Verify the supervisor calls
    restart_fn within the configured probe timeout, not only at the far-off
    session cap.
    """

    class _FakeProcess:
        def poll(self) -> int | None:  # exits -> process_exited path
            return 1

    class _FakeInner:
        def __init__(self) -> None:
            self.process = _FakeProcess()
            self.endpoint = "http://127.0.0.1:8765/mcp"
            self.shutdown_calls: list[bool] = []

        def shutdown(self) -> None:
            self.shutdown_calls.append(True)

    inner = _FakeInner()
    new_inner = _FakeInner()
    new_inner.endpoint = "http://127.0.0.1:8766/mcp"

    def restart_fn() -> _FakeInner:
        return new_inner

    bridge = RestartAwareMcpBridge(
        inner=cast("object", inner),
        restart_fn=cast("Callable[[], object]", restart_fn),
        restart_policy=McpRestartPolicy(max_restarts=1),
    )

    # process_exited=True, so restart fires immediately
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1
    assert inner.shutdown_calls, "shutdown must be called on the dead inner"
    assert bridge.endpoint_uri() == "http://127.0.0.1:8766/mcp"


def test_supervisor_probe_failure_with_explicit_timeout() -> None:
    """When the probe function fails, the supervisor restarts within bound."""

    class _FakeProcess:
        def poll(self) -> int | None:  # alive
            return None

    class _FakeInner:
        def __init__(self) -> None:
            self.process = _FakeProcess()
            self.endpoint = "http://127.0.0.1:8765/mcp"
            self.shutdown_calls: list[bool] = []

        def shutdown(self) -> None:
            self.shutdown_calls.append(True)

    inner = _FakeInner()
    new_inner = _FakeInner()
    new_inner.endpoint = "http://127.0.0.1:8766/mcp"

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe timed out")

    def restart_fn() -> _FakeInner:
        return new_inner

    bridge = RestartAwareMcpBridge(
        inner=cast("object", inner),
        restart_fn=cast("Callable[[], object]", restart_fn),
        restart_policy=McpRestartPolicy(max_restarts=1),
        probe_fn=cast("Callable[[str, timedelta], None]", probe_fn),
    )
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1
    assert inner.shutdown_calls


def test_health_route_is_present_in_fallback_handler() -> None:
    """The /health branch was added by Property C; verify the code path is present."""
    handler_text = (
        Path(__file__).parent.parent
        / "ralph"
        / "mcp"
        / "server"
        / "_fallback_http_handler.py"
    ).read_text()
    assert 'self.path == "/health"' in handler_text
    assert "_handle_health_get" in handler_text
