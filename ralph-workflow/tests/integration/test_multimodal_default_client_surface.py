"""Regression coverage for the default harness-visible multimodal surface."""

from __future__ import annotations

from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.names import READ_IMAGE_TOOL, READ_MEDIA_TOOL
from ralph.prompts import template_variables
from ralph.prompts._capability_set import CapabilitySet
from ralph.prompts._policy_flag_set import PolicyFlagSet
from ralph.workspace.memory import MemoryWorkspace

_MEDIA_CAPABILITIES = {
    "workspace.read",
    "workspace.write_tracked",
    "workspace.metadata_read",
    "workspace.edit",
    "workspace.delete",
    "git.status_read",
    "git.diff_read",
    "process.exec_bounded",
    "artifact.submit",
    "run.report_progress",
    "env.read",
    "web.search",
    "web.visit",
    "web.download",
    "media.read",
}


def _build_server() -> McpServer:
    session = AgentSession(
        session_id="default-surface-session",
        run_id="default-surface-run",
        drain="development",
        capabilities=_MEDIA_CAPABILITIES,
    )
    workspace = MemoryWorkspace()
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _initialize(server: McpServer) -> ServerState:
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        msg_id=1,
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "default-harness", "version": "1.0"},
        },
    )
    response, state = server.handle_request(request, ServerState.UNINITIALIZED)
    assert response is not None and response.result is not None
    return state


def _list_tool_names(server: McpServer, state: ServerState) -> set[str]:
    response, _ = server.handle_request(
        JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )
    assert response is not None and response.result is not None
    tools = response.result["tools"]
    assert isinstance(tools, list)
    return {tool["name"] for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)}


def test_mcp_multimodal_regression_default_harness_surface() -> None:
    """Plan S-2: a normal harness handshake advertises enabled media tools."""
    server = _build_server()
    state = _initialize(server)
    tool_names = _list_tool_names(server, state)

    assert READ_MEDIA_TOOL in tool_names
    assert READ_IMAGE_TOOL in tool_names


def test_prompt_multimodal_regression_advertises_media_tools() -> None:
    """Plan S-2: media-capable prompt catalogs retain tool names and references."""
    visible = set(
        visible_tool_names_for_capabilities(
            _MEDIA_CAPABILITIES,
            drain=SessionDrain.DEVELOPMENT.value,
        )
    )
    assert READ_MEDIA_TOOL in visible
    assert READ_IMAGE_TOOL in visible

    variables = template_variables.capability_template_variables(
        CapabilitySet.from_identifiers(_MEDIA_CAPABILITIES),
        PolicyFlagSet.defaults_for_drain(SessionDrain.DEVELOPMENT),
    )
    assert variables["READ_MEDIA_TOOL_REFERENCE"]
    assert variables["READ_IMAGE_TOOL_REFERENCE"]
