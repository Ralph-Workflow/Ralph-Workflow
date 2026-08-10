"""Integration tests for the standalone Python MCP server runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.mcp_models import McpConfig, MediaConfig
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.upstream.config import UPSTREAM_MCP_CONFIG_ENV
from ralph.workspace.fs import FsWorkspace
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str

_MEDIA_CAPABILITIES = {
    "WorkspaceRead",
    "ArtifactSubmit",
    "ArtifactPlanWrite",
    "RunReportProgress",
    "WorkspaceWriteAny",
    "WorkspaceMetadataRead",
    "WorkspaceEdit",
    "WorkspaceDelete",
    "GitStatusRead",
    "GitDiffRead",
    "ProcessExecBounded",
    "EnvRead",
    "WebSearch",
    "WebVisit",
    "WebDownload",
    "media.read",
}


@pytest.fixture(autouse=True)
def _isolate_from_upstream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UPSTREAM_MCP_CONFIG_ENV, raising=False)


def _server(tmp_path: Path, config: McpConfig) -> server_runtime.McpServer:
    session = AgentSession(
        session_id="session-multimodal",
        run_id="run-multimodal",
        drain="development",
        capabilities=_MEDIA_CAPABILITIES,
    )
    workspace = FsWorkspace(tmp_path)
    bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
    return server_runtime.McpServer(session, workspace, bridge)


def _tool_names(server: server_runtime.McpServer) -> set[str]:
    _, state = server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="initialize",
            msg_id=1,
            params={"capabilities": {}},
        ),
        server_runtime.ServerState.UNINITIALIZED,
    )
    response, _ = server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )
    assert response is not None
    tools_result = must_mapping(response.result)
    return {must_str(tool["name"]) for tool in must_dict_list(tools_result["tools"])}


def test_default_media_surface_is_visible_without_client_capability(tmp_path: Path) -> None:
    """A default empty MCP handshake still exposes enabled media tools."""
    tool_names = _tool_names(_server(tmp_path, McpConfig()))

    assert "read_media" in tool_names
    assert "read_image" in tool_names


def test_explicit_media_disabled_removes_media_tools(tmp_path: Path) -> None:
    """The [media] enabled=false configuration is the media visibility opt-out."""
    config = McpConfig(media=MediaConfig(enabled=False))
    tool_names = _tool_names(_server(tmp_path, config))

    assert "read_media" not in tool_names
    assert "read_image" not in tool_names


def test_media_toggle_preserves_text_tool_surface(tmp_path: Path) -> None:
    """Changing media registration does not alter the existing text-tool names."""
    enabled_names = _tool_names(_server(tmp_path, McpConfig()))
    disabled_names = _tool_names(
        _server(tmp_path, McpConfig(media=MediaConfig(enabled=False)))
    )

    assert "read_file" in enabled_names
    assert "read_file" in disabled_names
    media_names = {
        "read_media",
        "read_image",
        "mcp__ralph__read_media",
        "mcp__ralph__read_image",
    }
    assert disabled_names == enabled_names - media_names
