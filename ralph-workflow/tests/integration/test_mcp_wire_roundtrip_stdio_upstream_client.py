"""Integration tests for MCP wire-level roundtrip.

- HTTP transport tests: replaced with in-process McpServer.handle_request() calls —
  no daemon threads, no socket polling, no httpx against localhost.
- stdio transport: uses the fake stdio server fixture to test Ralph's MCP client.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.workspace.fs import FsWorkspace

pytestmark = pytest.mark.subprocess_e2e

# Description length bounds enforced by the quality bar
_MIN_DESCRIPTION_CHARS = 20
_MAX_DESCRIPTION_CHARS = 500

# Content used by the append_file roundtrip assertion
_APPEND_CONTENT = "hi"

# Capabilities required by the built-in Ralph tools
_REQUIRED_CAPABILITIES = {
    "WorkspaceRead",
    "WorkspaceWriteAny",
    "WorkspaceMetadataRead",
    "WorkspaceEdit",
    "WorkspaceDelete",
    "GitStatusRead",
    "ProcessExecBounded",
    "ArtifactSubmit",
    "RunReportProgress",
    "EnvRead",
    "WebSearch",
    "WebVisit",
}


def _build_server(
    workspace_path: Path,
    *,
    session_id: str = "test-session",
    drain: str = "test",
) -> McpServer:
    workspace = FsWorkspace(workspace_path)
    session = AgentSession(
        session_id=session_id,
        run_id="test-run",
        drain=drain,
        capabilities=_REQUIRED_CAPABILITIES,
    )
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _do_initialize(server: McpServer) -> ServerState:
    """Send initialize + notifications/initialized; return running ServerState."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.00"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None, "initialize returned None"
    init_result = cast("dict[str, Any]", resp.result)
    assert init_result["protocolVersion"] == "2024-11-05"
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    none_resp, state = server.handle_request(notif, state)
    assert none_resp is None
    return state


def _do_tools_list(server: McpServer, state: ServerState) -> list[dict[str, object]]:
    req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", params={}, msg_id=2)
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None, f"tools/list failed: {resp}"
    return cast("list[dict[str, Any]]", cast("dict[str, Any]", resp.result)["tools"])


def _do_tool_call(
    server: McpServer,
    state: ServerState,
    call_id: list[int],
    name: str,
    args: dict[str, object],
) -> dict[str, object]:
    """Make a tools/call request and return the MCP result object."""
    call_id[0] += 1
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": name, "arguments": args},
        msg_id=call_id[0],
    )
    resp, _ = server.handle_request(req, state)
    assert resp is not None, f"tools/call {name!r} returned None"
    return cast("dict[str, Any]", resp.result)


@pytest.mark.integration
@pytest.mark.subprocess_e2e
class TestStdioUpstreamClient:
    """Test Ralph's MCP client code path using the fake stdio fixture."""

    def test_list_tools_from_fake_stdio_server(self) -> None:
        """make_upstream_client lists tools from the fake stdio server."""
        fake_stdio_path = Path(__file__).parent.parent / "fixtures" / "fake_stdio_mcp.py"

        server = UpstreamMcpServer(
            name="fake",
            transport="stdio",
            command=sys.executable,
            args=(str(fake_stdio_path),),
        )

        client = make_upstream_client(server)
        tools = client.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "fake_tool"
        assert tools[0].description == "A fake tool for testing"

    def test_call_tool_on_fake_stdio_server(self) -> None:
        """call_tool on the fake stdio server returns the expected response shape."""
        fake_stdio_path = Path(__file__).parent.parent / "fixtures" / "fake_stdio_mcp.py"

        server = UpstreamMcpServer(
            name="fake",
            transport="stdio",
            command=sys.executable,
            args=(str(fake_stdio_path),),
        )

        client = make_upstream_client(server)
        result = client.call_tool("fake_tool", {})

        assert result is not None
        as_dict = getattr(result, "to_dict", None)
        result_dict = as_dict() if as_dict else result
        assert isinstance(result_dict, dict)
        content = result_dict.get("content", [])
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0].get("type") == "text"
        assert content[0].get("text") == "fake-result"
        assert result_dict.get("isError") is False
