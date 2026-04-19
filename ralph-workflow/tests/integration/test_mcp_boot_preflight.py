"""Integration test for MCP server boot preflight: web_search + upstream tools coexist."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from ralph.mcp.server.lifecycle import shutdown_mcp_server, start_mcp_server
from ralph.mcp.session import AgentSession
from ralph.mcp.upstream_config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    serialize_upstream_mcp_servers,
)
from ralph.workspace.fs import FsWorkspace

HTTP_OK = 200


@pytest.fixture
def workspace_with_mcp_config(tmp_path: Path) -> Path:
    """Create a temp workspace with .agent/mcp.toml that enables web_search."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    mcp_toml = agent_dir / "mcp.toml"
    mcp_toml.write_text("[web_search]\nenabled = true\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_upstream_config(workspace_with_mcp_config: Path) -> UpstreamMcpServer:
    """Build an UpstreamMcpServer pointing at the fake stdio MCP script."""
    fake_script = (
        Path(__file__).parent.parent.parent
        / "tests"
        / "fixtures"
        / "fake_stdio_mcp.py"
    ).resolve()
    return UpstreamMcpServer(
        name="fake",
        transport="stdio",
        command=sys.executable,
        args=(str(fake_script),),
    )


@pytest.fixture
def upstream_config_env(fake_upstream_config: UpstreamMcpServer) -> str:
    """Serialize fake upstream config as an env var value."""
    return serialize_upstream_mcp_servers([fake_upstream_config])


def test_server_boot_exposes_both_web_search_and_upstream(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    workspace_with_mcp_config: Path,
    upstream_config_env: str,
) -> None:
    """Verify MCP server exposes both web_search and upstream tools after boot.

    This test validates T12 Part C:
    - RALPH_UPSTREAM_MCP_CONFIG is set to a serialized fake upstream
    - .agent/mcp.toml has [web_search] enabled = true
    - start_mcp_server() is called and the server is queried via httpx
    - tools/list returns both web_search and ralph_upstream__fake__fake_tool
    - shutdown_mcp_server() is called in finalizer for cleanup
    """
    monkeypatch.setenv(UPSTREAM_MCP_CONFIG_ENV, upstream_config_env)

    session = AgentSession(
        session_id="test-boot-preflight",
        run_id="run-boot-preflight",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit", "UpstreamToolUse"},
    )
    workspace = FsWorkspace(workspace_with_mcp_config)

    bridge = start_mcp_server(session, workspace)

    request.addfinalizer(lambda: shutdown_mcp_server(bridge))

    endpoint = bridge.agent_endpoint_uri()

    with httpx.Client(timeout=30.0) as client:
        init_response = client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {}},
            },
        )
        assert init_response.status_code == HTTP_OK, f"initialize failed: {init_response.text}"
        init_data = init_response.json()
        assert init_data.get("id") == 1
        assert "result" in init_data

        client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": None,
                "method": "notifications/initialized",
                "params": {},
            },
        )

        tools_response = client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert tools_response.status_code == HTTP_OK, f"tools/list failed: {tools_response.text}"
        tools_data = tools_response.json()
        assert "result" in tools_data, f"Expected result in response: {tools_data}"
        result = tools_data["result"]
        assert "tools" in result, f"Expected tools in result: {result}"
        tools = result["tools"]
        tool_names = [t["name"] for t in tools]

        assert "web_search" in tool_names, (
            f"web_search not found in tools: {tool_names}"
        )
        assert "ralph_upstream__fake__fake_tool" in tool_names, (
            f"ralph_upstream__fake__fake_tool not found in tools: {tool_names}"
        )
