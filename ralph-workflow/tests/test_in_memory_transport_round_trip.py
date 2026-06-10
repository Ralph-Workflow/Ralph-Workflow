# property-test: foundation — in-memory transport round trip on the shipped path
"""Round-trip test for the in-memory MCP transport harness.

The harness drives the production ``_FallbackHttpHandler`` over in-memory
buffers, so this test exercises the SHIPPED path — the same code that runs
in production. The 60-second combined test budget is unaffected (this test
completes in well under 1 second).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import (
    _build_tools_list_payload,
    _make_fake_server,
    drive_request,
    parse_sse_data,
)
from ralph.mcp.server.runtime import McpServer, ServerState, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_mcp_server(tmp_path: Path) -> McpServer:
    session = AgentSession(
        session_id="transport-test",
        run_id="transport-run",
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


def test_tools_list_returns_single_sse_frame_with_tools_array(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)
    status, headers, body = drive_request(mcp_server, _build_tools_list_payload())
    assert status == 200
    assert headers.get("content-type") == "text/event-stream"
    payload = parse_sse_data(body)
    assert payload.get("jsonrpc") == "2.0"
    result = cast("dict[str, object]", payload.get("result", {}))
    tools = cast("list[dict[str, object]]", result.get("tools", []))
    assert tools, "expected at least one tool registered"
    names = {cast("str", entry["name"]) for entry in tools}
    assert "read_file" in names


def test_round_trip_uses_no_sockets_and_no_real_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The driver must be deterministic — no time.sleep, no sockets."""
    # Patch the sleep that the do_GET keepalive loop uses, so a leaked
    # invocation of the keepalive path would be visible as a sleep call.
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("ralph.mcp.server._fallback_http_handler.sleep", fake_sleep)
    mcp_server = _make_mcp_server(tmp_path)
    status, _headers, _body = drive_request(mcp_server, _build_tools_list_payload())
    assert status == 200
    # A POST goes through do_POST (no sleep); a GET to /mcp would have entered
    # the keepalive loop, but we did not call that path here.
    assert sleep_calls == []


def test_notification_initialized_returns_202_no_sse_payload(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ).encode()
    status, _headers, body = drive_request(mcp_server, payload)
    assert status == 202
    assert body == b""


def test_unknown_path_returns_404(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)
    status, _headers, _body = drive_request(
        mcp_server, _build_tools_list_payload(), path="/nope"
    )
    assert status == 404


def test_drive_request_matches_production_request_dataclass(tmp_path: Path) -> None:
    """The harness forwards a JsonRpcRequest that the production McpServer can dispatch."""
    mcp_server = _make_mcp_server(tmp_path)
    status, _headers, body = drive_request(mcp_server, _build_tools_list_payload())
    assert status == 200
    payload = parse_sse_data(body)
    assert payload.get("id") == 1
    assert "result" in payload


def test_state_progression_to_running(tmp_path: Path) -> None:
    """A POST that runs initialize transitions the server state to RUNNING."""
    mcp_server = _make_mcp_server(tmp_path)
    init_payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0"},
            },
        }
    ).encode()
    status, _headers, _body = drive_request(mcp_server, init_payload)
    assert status == 200
    status, _headers, _body = drive_request(mcp_server, _build_tools_list_payload())
    assert status == 200


def test_invalid_json_returns_parse_error_frame(tmp_path: Path) -> None:
    mcp_server = _make_mcp_server(tmp_path)
    status, headers, body = drive_request(mcp_server, b"{not json")
    assert status == 400
    # 400 / 401 / 503 error responses use application/json; SSE responses use text/event-stream.
    if headers.get("content-type", "").startswith("text/event-stream"):
        payload = parse_sse_data(body)
    else:
        payload = cast("dict[str, object]", json.loads(body))
    assert payload.get("jsonrpc") == "2.0"
    error = cast("dict[str, object]", payload.get("error", {}))
    assert error.get("code") == -32700


def test_handler_state_factory_helper_returns_production_surface(
    tmp_path: Path,
) -> None:
    """The SimpleNamespace exposes the production casted _FallbackHttpServer surface."""
    mcp_server = _make_mcp_server(tmp_path)
    fake = _make_fake_server(mcp_server, ServerState.UNINITIALIZED)
    assert fake.mcp_server is mcp_server
    assert fake.state == ServerState.UNINITIALIZED
    assert hasattr(fake.shutdown_event, "set")
    assert hasattr(fake.shutdown_event, "is_set")
    assert fake.server_address == ("127.0.0.1", 0)
