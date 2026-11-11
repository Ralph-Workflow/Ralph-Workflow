from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

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
from tests._support.typed_accessors import (
    must_mapping,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
    from ralph.mcp.server._server_state import ServerState


class _ServerWithHealthProbe(Protocol):
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
    factory = _original if _original is not None else _in_memory_transport._make_fake_server
    fake = factory(mcp_server, state)
    typed: _ServerWithHealthProbe = fake
    if probe is not None:
        typed.health_probe_fn = probe
    if metrics is not None:
        typed.metrics = metrics
    return fake


def test_health_route_returns_200_healthy_when_probe_succeeds(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)

    def healthy_probe() -> _ProbeResult:
        return _ProbeResult(healthy=True, latency_ms=12.5, reason="")

    original = _in_memory_transport._make_fake_server
    _in_memory_transport._make_fake_server = lambda m, s: _make_with_probe(
        m, s, probe=healthy_probe, _original=original
    )
    try:
        status, headers, body = drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    assert status == 200, f"expected 200, got {status}; body={body!r}"
    assert "application/json" in headers.get("content-type", "")
    payload = must_mapping(json.loads(body))
    assert payload.get("status") == "healthy"
    assert payload.get("latency_ms") == 12.5


def test_health_route_returns_503_unhealthy_when_probe_raises(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)

    def broken_probe() -> _ProbeResult:
        raise RuntimeError("probe went sideways")

    original = _in_memory_transport._make_fake_server
    _in_memory_transport._make_fake_server = lambda m, s: _make_with_probe(
        m, s, probe=broken_probe, _original=original
    )
    try:
        status, headers, body = drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    assert status == 503, f"expected 503, got {status}; body={body!r}"
    assert "application/json" in headers.get("content-type", "")
    payload = must_mapping(json.loads(body))
    assert payload.get("status") == "unhealthy"
    assert "RuntimeError" in str(payload.get("reason", ""))


def test_health_route_returns_503_when_probe_reports_unhealthy(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)

    def unhealthy_probe() -> _ProbeResult:
        return _ProbeResult(healthy=False, latency_ms=0.0, reason="server_wedged")

    original = _in_memory_transport._make_fake_server
    _in_memory_transport._make_fake_server = lambda m, s: _make_with_probe(
        m, s, probe=unhealthy_probe, _original=original
    )
    try:
        status, _headers, body = drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    assert status == 503
    payload = must_mapping(json.loads(body))
    assert payload.get("status") == "unhealthy"
    assert payload.get("reason") == "server_wedged"


def test_health_route_increments_health_probe_outcomes_counter(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)
    metrics = McpMetrics()
    original = _in_memory_transport._make_fake_server

    def _probe() -> _ProbeResult:
        return _ProbeResult(healthy=True, latency_ms=1.0, reason="")

    _in_memory_transport._make_fake_server = lambda m, s: _make_with_probe(
        m, s, probe=_probe, metrics=metrics, _original=original
    )
    try:
        drive_request(mcp_server, b"", path="/health", method="GET")
        drive_request(mcp_server, b"", path="/health", method="GET")
    finally:
        _in_memory_transport._make_fake_server = original
    snapshot = metrics.snapshot()
    outcomes = snapshot["health_probe_outcomes"]
    assert outcomes["success"] >= 2


def test_supervisor_calls_restart_within_configured_probe_timeout() -> None:

    class _FakeProcess:
        def poll(self) -> int | None:
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
        inner=inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="test-run",
    )

    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1
    assert inner.shutdown_calls, "shutdown must be called on the dead inner"
    assert bridge.endpoint_uri() == "http://127.0.0.1:8766/mcp"


def test_supervisor_probe_failure_with_explicit_timeout() -> None:

    class _FakeProcess:
        def poll(self) -> int | None:
            return None

        @property
        def pid(self) -> int:
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

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe timed out")

    def restart_fn() -> _FakeInner:
        return new_inner

    bridge = RestartAwareMcpBridge(
        inner=inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
    )
    restarts = [bridge.check_health_and_restart_if_needed() for _ in range(3)]
    assert restarts == [False, False, True]
    assert bridge.restart_count == 1
    assert inner.shutdown_calls


def test_health_route_is_present_in_fallback_handler() -> None:
    handler_text = (
        Path(__file__).parent.parent / "ralph" / "mcp" / "server" / "_fallback_http_handler.py"
    ).read_text()
    assert 'self.path == "/health"' in handler_text
    assert "_handle_health_get" in handler_text
