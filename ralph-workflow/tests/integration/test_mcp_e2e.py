"""Tests for MCP server configuration, upstream proxy, and web search behavior.

Each test drives the server in-process via McpServer.handle_request() — no threads,
no sockets, no wall-clock waits.  All five behaviors are preserved at the public
JSON-RPC request/response seam.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

import ralph.mcp.websearch.backends.ddgs as _ddgs_mod

if TYPE_CHECKING:
    import pytest

from ralph.config.mcp_models import McpConfig, WebSearchBackendSpec, WebSearchConfig
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tools.names import WEB_SEARCH_TOOL, upstream_proxy_tool_name
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.mcp.upstream.registry import ProxiedTool, UpstreamRegistry
from ralph.workspace.memory import MemoryWorkspace

BROKEN_CANARY = "sk-fake-leak-canary-test-12345"

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
    "UpstreamToolUse",
}


def _make_session(session_id: str = "test-session") -> AgentSession:
    return AgentSession(
        session_id=session_id,
        run_id="test-run",
        drain="test",
        capabilities=_REQUIRED_CAPABILITIES,
    )


def _fake_upstream_registry(server_name: str, *, tool_name: str = "fake_tool") -> UpstreamRegistry:
    """Return an UpstreamRegistry with one proxied fake tool — no subprocess."""
    tool = UpstreamTool(name=tool_name, description="A fake tool for testing")
    alias = upstream_proxy_tool_name(server_name, tool_name)
    return UpstreamRegistry([ProxiedTool(alias=alias, server_name=server_name, tool=tool)], {})


def _unreachable_upstream_registry(server_name: str) -> UpstreamRegistry:
    """Build an UpstreamRegistry where the upstream fails to connect.

    Uses a custom client_factory that raises UpstreamCallError on list_tools()
    so UpstreamRegistry.build() emits the warning and returns an empty registry
    without spawning any real subprocess.
    """
    server = UpstreamMcpServer(name=server_name, transport="stdio", command="/not/a/real/command")

    class _FailingClient:
        def list_tools(self) -> list[UpstreamTool]:
            raise UpstreamCallError("connection refused: /not/a/real/command")

        def call_tool(self, name: str, args: object) -> object:
            raise UpstreamCallError("not connected")

    return UpstreamRegistry.build(
        [server],
        client_factory=lambda _s: _FailingClient(),
        on_unreachable="warn_and_skip",
    )


def _build_mcp_server(
    mcp_config: McpConfig | None = None,
    upstream_registry: UpstreamRegistry | None = None,
    session_id: str = "test-session",
) -> McpServer:
    """Build a McpServer with MemoryWorkspace — no sockets, no threads."""
    session = _make_session(session_id)
    workspace = MemoryWorkspace()
    registry = build_ralph_tool_registry(
        session,
        workspace,
        mcp_config=mcp_config or McpConfig(),
        upstream_registry=upstream_registry,
    )
    return McpServer(session, workspace, registry)


def _initialize(server: McpServer) -> ServerState:
    """Send initialize + notifications/initialized; return the running ServerState."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None and resp.result is not None, f"initialize failed: {resp}"
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    none_resp, state = server.handle_request(notif, state)
    assert none_resp is None
    return state


def _list_tools(server: McpServer, state: ServerState) -> list[dict[str, object]]:
    """Call tools/list and return the tools array."""
    req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", params={}, msg_id=2)
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None, f"tools/list failed: {resp}"
    return cast("list[dict[str, Any]]", cast("dict[str, Any]", resp.result)["tools"])


def _call_tool(
    server: McpServer,
    state: ServerState,
    name: str,
    arguments: dict[str, object],
    *,
    msg_id: int = 3,
) -> dict[str, object]:
    """Call tools/call and return the result payload."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": name, "arguments": arguments},
        msg_id=msg_id,
    )
    resp, _ = server.handle_request(req, state)
    assert resp is not None, f"tools/call {name!r} returned None response"
    return cast("dict[str, Any]", resp.result)


def test_mcp_server_boots_with_mcp_toml_and_custom_upstream() -> None:
    mcp_config = McpConfig(web_search=WebSearchConfig(enabled=True))
    upstream_reg = _fake_upstream_registry("fake_stdio")
    server = _build_mcp_server(mcp_config=mcp_config, upstream_registry=upstream_reg)
    state = _initialize(server)
    tool_names = {tool["name"] for tool in _list_tools(server, state)}
    assert WEB_SEARCH_TOOL in tool_names
    assert upstream_proxy_tool_name("fake_stdio", "fake_tool") in tool_names


def test_web_search_end_to_end_with_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDdgsClient:
        def text(self, query: str, max_results: int) -> list[dict[str, object]]:
            return [
                {
                    "title": f"Mocked Title {i + 1} for {query}",
                    "href": f"https://example.com/{i + 1}",
                    "body": f"Snippet {i + 1}",
                }
                for i in range(max_results)
            ]

    monkeypatch.setattr(_ddgs_mod, "DDGS", _FakeDdgsClient)
    mcp_config = McpConfig(web_search=WebSearchConfig(enabled=True, backend="ddgs"))
    server = _build_mcp_server(mcp_config=mcp_config)
    state = _initialize(server)
    result = _call_tool(server, state, WEB_SEARCH_TOOL, {"query": "test", "limit": 2})
    assert result["isError"] is False
    rendered = json.dumps(result["content"])
    assert "Mocked Title 1 for test" in rendered
    assert "Mocked Title 2 for test" in rendered


def test_web_search_fallback_chain_under_real_config() -> None:
    mcp_config = McpConfig(
        web_search=WebSearchConfig(enabled=True, backend="ddgs", fallback=["ddgs"])
    )
    upstream_reg = _fake_upstream_registry("fake_stdio")
    server = _build_mcp_server(mcp_config=mcp_config, upstream_registry=upstream_reg)
    state = _initialize(server)
    tool_names = {tool["name"] for tool in _list_tools(server, state)}
    assert WEB_SEARCH_TOOL in tool_names
    assert upstream_proxy_tool_name("fake_stdio", "fake_tool") in tool_names


def test_unreachable_upstream_emits_warning_and_server_starts() -> None:
    mcp_config = McpConfig(web_search=WebSearchConfig(enabled=True))
    captured: list[str] = []
    sink_id = logger.add(captured.append, format="{message}")
    try:
        upstream_reg = _unreachable_upstream_registry("fake_stdio")
        server = _build_mcp_server(mcp_config=mcp_config, upstream_registry=upstream_reg)
        state = _initialize(server)
        tool_names = {tool["name"] for tool in _list_tools(server, state)}
    finally:
        logger.remove(sink_id)
    log_output = "\n".join(captured)
    assert WEB_SEARCH_TOOL in tool_names
    assert not any(name.startswith("ralph_upstream__fake_stdio__") for name in tool_names)
    assert "Skipping upstream MCP server fake_stdio" in log_output


def test_secret_never_in_e2e_logs() -> None:
    mcp_config = McpConfig(
        web_search=WebSearchConfig(
            enabled=True,
            backend="ddgs",
            backends={"tavily": WebSearchBackendSpec(backend="tavily", api_key=BROKEN_CANARY)},
        )
    )
    captured: list[str] = []
    sink_id = logger.add(captured.append, format="{message}")
    try:
        server = _build_mcp_server(mcp_config=mcp_config)
        state = _initialize(server)
        _list_tools(server, state)
    finally:
        logger.remove(sink_id)
    log_output = "\n".join(captured)
    assert BROKEN_CANARY not in log_output


# ---------------------------------------------------------------------------
# Multimodal capability-aware tool visibility and resource template tests
# ---------------------------------------------------------------------------

_MEDIA_CAPABILITIES = _REQUIRED_CAPABILITIES | {"media.read"}


def _build_multimodal_server(session_id: str = "test-multimodal") -> McpServer:
    """Build a McpServer with media.read session capability."""
    from ralph.mcp.protocol.session import AgentSession

    session = AgentSession(
        session_id=session_id,
        run_id="test-run",
        drain="test",
        capabilities=_MEDIA_CAPABILITIES,
    )
    workspace = MemoryWorkspace()
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _initialize_with_multimodal_caps(server: McpServer) -> ServerState:
    """Send initialize declaring multimodal client capabilities; return running ServerState."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {"media": {}, "image": {}},
            "clientInfo": {"name": "test-multimodal", "version": "1.0"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None and resp.result is not None, f"initialize failed: {resp}"
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    none_resp, state = server.handle_request(notif, state)
    assert none_resp is None
    return state


def test_multimodal_client_sees_read_media_and_read_image_in_tools_list() -> None:
    """Multimodal-capable client sees read_media and read_image in tools/list."""
    server = _build_multimodal_server()
    state = _initialize_with_multimodal_caps(server)
    tool_names = {tool["name"] for tool in _list_tools(server, state)}
    assert "read_media" in tool_names, f"read_media missing from tools: {sorted(tool_names)}"
    assert "read_image" in tool_names, f"read_image missing from tools: {sorted(tool_names)}"


def test_text_only_client_does_not_see_read_media_in_tools_list() -> None:
    """Text-only client (no multimodal capability) does not see read_media or read_image."""
    server = _build_multimodal_server()
    state = _initialize(server)
    tool_names = {tool["name"] for tool in _list_tools(server, state)}
    assert "read_media" not in tool_names, "read_media should be hidden from text-only client"
    assert "read_image" not in tool_names, "read_image should be hidden from text-only client"


def test_resource_templates_list_includes_media_template_when_media_read_is_granted() -> None:
    """resources/templates/list exposes ralph://media/{artifact_id} when media.read is granted."""
    server = _build_multimodal_server()
    state = _initialize_with_multimodal_caps(server)
    req = JsonRpcRequest(jsonrpc="2.0", method="resources/templates/list", params={}, msg_id=5)
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None, f"resource templates/list failed: {resp}"
    result = cast("dict[str, Any]", resp.result)
    templates = cast("list[dict[str, Any]]", result.get("resourceTemplates", []))
    uri_templates = {t.get("uriTemplate") for t in templates}
    assert "ralph://media/{artifact_id}" in uri_templates, (
        f"Expected ralph://media/{{artifact_id}} in resourceTemplates, got: {uri_templates}"
    )
