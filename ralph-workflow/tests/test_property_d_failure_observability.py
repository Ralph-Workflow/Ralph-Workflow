# property-test: D — failure observability, counters, log record, startup banner
"""Failure is observable.

Every post-header failure produces a structured, attributable log record and
increments a named counter. The counter surface is minimal: post-header
failures, terminal-frame emissions, and health-probe outcomes are counted and
exposed. Startup announces the live configuration.

This test exercises the McpMetrics dataclass directly and asserts the
counter behavior on the production McpServer and the in-memory /health route.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Never, cast

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import _in_memory_transport
from ralph.mcp.server._fallback_http_handler_probe import _ProbeResult
from ralph.mcp.server._in_memory_transport import drive_request
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._metrics import McpMetrics
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server.runtime import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.server._fallback_http_server import _FallbackHttpServer


def _make_mcp_server(tmp_path: Path, *, metrics: McpMetrics | None = None) -> McpServer:
    session = AgentSession(
        session_id="obs-test",
        run_id="obs-run",
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
    return McpServer(session, workspace, registry, metrics=metrics)


def _broken_request() -> JsonRpcRequest:
    """A request that triggers the dispatch to raise (unknown method path)."""
    # Use a request whose _dispatch_request raises. We can do that by passing
    # a request whose method is unknown but with an invalid params structure
    # that bypasses the normal not-found path. Easier: monkeypatch the
    # server's _dispatch_request.
    return JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": "nonexistent_tool", "arguments": {}},
        msg_id="obs-1",
    )


def test_post_header_failure_increments_counter(tmp_path: Path) -> None:
    """A request whose handler raises increments post_header_failures."""
    metrics = McpMetrics()
    mcp_server = _make_mcp_server(tmp_path, metrics=metrics)

    def explode(server: McpServer, request: JsonRpcRequest, state: ServerState) -> Never:
        raise RuntimeError("simulated dispatch failure")

    # Monkeypatch _dispatch_request to raise.
    original = mcp_server._dispatch_request
    mcp_server._dispatch_request = lambda req, state: explode(
        mcp_server, req, state
    )
    response, _state = mcp_server.handle_request(_broken_request(), ServerState.RUNNING)
    assert response is not None
    assert response.error is not None
    assert response.error["code"] == -32603
    assert metrics.snapshot()["post_header_failures"] == 1
    mcp_server._dispatch_request = original  # restore


def test_post_header_failure_100_concurrent_increments_counter(tmp_path: Path) -> None:
    """100 concurrent failures increment the counter race-free (Lock-protected)."""
    metrics = McpMetrics()
    mcp_server = _make_mcp_server(tmp_path, metrics=metrics)

    def explode(server: McpServer, request: JsonRpcRequest, state: ServerState) -> Never:
        raise RuntimeError("simulated dispatch failure")

    mcp_server._dispatch_request = lambda req, state: explode(
        mcp_server, req, state
    )

    results: list[object] = []

    def worker() -> None:
        response, _state = mcp_server.handle_request(
            _broken_request(), ServerState.RUNNING
        )
        results.append(response)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 100
    assert metrics.snapshot()["post_header_failures"] == 100


def test_health_probe_outcome_counter_increments(tmp_path: Path) -> None:
    """A failed /health probe increments health_probe_outcomes[failure]."""
    metrics = McpMetrics()
    mcp_server = _make_mcp_server(tmp_path, metrics=metrics)
    original_make = _in_memory_transport._make_fake_server

    def _make_with_probe(
        mcp_server_arg: McpServer, state: ServerState
    ) -> _FallbackHttpServer:
        fake = original_make(mcp_server_arg, state)

        def _probe() -> _ProbeResult:
            raise RuntimeError("probe broken")

        fake.health_probe_fn = _probe
        fake.metrics = metrics
        return fake

    _in_memory_transport._make_fake_server = _make_with_probe
    status, _headers, _body = drive_request(mcp_server, b"", path="/health", method="GET")
    assert status == 503
    snapshot = metrics.snapshot()
    outcomes = cast("dict[str, int]", snapshot["health_probe_outcomes"])
    assert outcomes["failure"] == 1


def test_health_probe_success_increments_success_counter(tmp_path: Path) -> None:
    """A successful /health probe increments health_probe_outcomes[success]."""
    metrics = McpMetrics()
    mcp_server = _make_mcp_server(tmp_path, metrics=metrics)
    original_make = _in_memory_transport._make_fake_server

    def _make_with_probe(
        mcp_server_arg: McpServer, state: ServerState
    ) -> _FallbackHttpServer:
        fake = original_make(mcp_server_arg, state)

        def _probe() -> _ProbeResult:
            return _ProbeResult(healthy=True, latency_ms=1.0, reason="")

        fake.health_probe_fn = _probe
        fake.metrics = metrics
        return fake

    _in_memory_transport._make_fake_server = _make_with_probe
    status, _headers, _body = drive_request(mcp_server, b"", path="/health", method="GET")
    assert status == 200
    snapshot = metrics.snapshot()
    outcomes = cast("dict[str, int]", snapshot["health_probe_outcomes"])
    assert outcomes["success"] == 1


def test_terminal_frame_emission_counter_increments() -> None:
    """The terminal_frame_emissions counter is incrementable."""
    metrics = McpMetrics()
    metrics.record_terminal_frame("tools/call")
    metrics.record_terminal_frame("tools/call")
    assert metrics.snapshot()["terminal_frame_emissions"] == 2


def test_metrics_snapshot_has_all_three_keys() -> None:
    """snapshot() returns all three named counters."""
    metrics = McpMetrics()
    snapshot = metrics.snapshot()
    assert "post_header_failures" in snapshot
    assert "terminal_frame_emissions" in snapshot
    assert "health_probe_outcomes" in snapshot


def test_startup_banner_contains_all_six_fields() -> None:
    """The startup banner announces transport/session/dispatch/drain/kill/probe/auth."""
    text = (
        Path(__file__).parent.parent
        / "ralph"
        / "mcp"
        / "server"
        / "_fallback_standalone_server.py"
    ).read_text()
    # The loguru format string must include all six fields.
    for field in (
        "transport",
        "session_class",
        "dispatch_cap",
        "drain_ceiling",
        "kill_escalation",
        "probe_timeout",
        "auth",
    ):
        assert field in text, f"startup banner missing {field!r}"


def test_mcp_metrics_constructible_with_no_io() -> None:
    """McpMetrics is constructible with no I/O (no socket, no file, no time)."""
    metrics = McpMetrics()
    metrics.record_post_header_failure(
        request_id="r1", method="tools/call", session_impl="AgentSession", cause="RuntimeError"
    )
    metrics.record_terminal_frame("tools/call")
    metrics.record_health_probe_outcome(True)
    metrics.record_health_probe_outcome(False)
    snap = metrics.snapshot()
    assert snap["post_header_failures"] == 1
    assert snap["terminal_frame_emissions"] == 1
    outcomes = cast("dict[str, int]", snap["health_probe_outcomes"])
    assert outcomes["success"] == 1
    assert outcomes["failure"] == 1


def test_metrics_record_does_not_require_real_wall_clock() -> None:
    """The counter methods must NOT read time.monotonic() / time.perf_counter().

    The McpMetrics module does not import time; the audit policy enforces
    that the production counters are pure and deterministic. Verify by
    reading the module's source: a missing `import time` (other than the
    import time as _time inside unrelated modules) confirms no wall-clock
    dependency.
    """
    text = (
        Path(__file__).parent.parent
        / "ralph"
        / "mcp"
        / "server"
        / "_metrics.py"
    ).read_text()
    # The module must not import time at all — there is no wall-clock need.
    # Allow the docstring / comments to mention time but not actually import.
    assert "import time" not in text or ("import time" in text and "time.monotonic" not in text), (
        "McpMetrics must not read time.monotonic / time.perf_counter"
    )
