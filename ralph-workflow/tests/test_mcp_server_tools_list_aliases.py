"""Regression tests for MCP server tools/list alias exposure.

The live failure mode in the post-tool-result wedge is that the MCP server's
``tools/list`` returns raw tool names (e.g. ``read_file``) but Claude Code's
strict MCP mode only invokes tools by their ``mcp__<server>__<tool>`` alias
(e.g. ``mcp__ralph__read_file``). The call comes back as
``No such tool available: mcp__ralph__read_file`` and the agent wedges.

These tests pin the dual-alias exposure rule and the alias-to-canonical
dispatch resolver so the bug cannot silently regress.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, claude_tool_name


class _NoopHandler:
    def __call__(
        self, session: object, workspace: object, params: dict[str, object]
    ) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}


def _build_server_with_tool(name: str = "read_file") -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name=name,
                description=f"Test tool {name}",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
            required_capability="workspace.read",
        ),
        cast("Any", _NoopHandler()),
    )
    return McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=bridge,
    )


def _tools_list(server: McpServer) -> list[dict[str, object]]:
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})
    response, _ = server._handle_tools_list(request)
    assert response.result is not None
    return cast("list[dict[str, object]]", response.result["tools"])


def test_tools_list_emits_raw_and_alias_entries_for_known_tool() -> None:
    server = _build_server_with_tool("read_file")
    tools = _tools_list(server)
    names = {t["name"] for t in tools}
    assert "read_file" in names
    expected_alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    assert expected_alias in names
    assert expected_alias == f"mcp__{RALPH_MCP_SERVER_NAME}__read_file"


def test_tools_list_alias_matches_canonical_description_and_input_schema() -> None:
    server = _build_server_with_tool("read_file")
    tools = _tools_list(server)
    raw = next(t for t in tools if t["name"] == "read_file")
    alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    alias_entry = next(t for t in tools if t["name"] == alias)
    assert alias_entry["description"] == raw["description"]
    assert alias_entry["inputSchema"] == raw["inputSchema"]


class _DuplicateNameRegistry:
    """A registry that emits two definitions sharing one name."""

    def list_definitions(self) -> list[ToolDefinition]:
        definition = ToolDefinition(
            name="dup",
            description="duplicate",
            input_schema={"type": "object"},
        )
        return [definition, definition]


def test_tools_list_runtime_invariant_rejects_duplicate_names() -> None:
    """If the tool surface regresses and emits a duplicate name, the runtime
    invariant must RAISE at _handle_tools_list (it stays loud even though
    handle_request now wraps it into a JSON-RPC error)."""
    server = McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=cast("Any", _DuplicateNameRegistry()),
    )
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})

    with pytest.raises(RuntimeError, match="duplicate tool names"):
        server._handle_tools_list(request)


def test_handle_request_wraps_duplicate_name_invariant_as_jsonrpc_error() -> None:
    """The same invariant, reached via handle_request, becomes a -32603 error
    rather than crashing the transport."""
    server = McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=cast("Any", _DuplicateNameRegistry()),
    )
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})

    response, _ = server.handle_request(request, ServerState.RUNNING)

    assert response is not None
    assert response.error is not None
    assert response.error["code"] == -32603


def test_tools_call_dispatches_alias_to_canonical_handler() -> None:
    server = _build_server_with_tool("read_file")
    alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": alias, "arguments": {"path": "/tmp/example.md"}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None, response.error
    assert response.result is not None


def test_tools_call_dispatches_raw_name_to_canonical_handler() -> None:
    server = _build_server_with_tool("read_file")
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": {"path": "/tmp/example.md"}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None, response.error


def test_tools_call_decodes_stringified_arguments_object_before_dispatch() -> None:
    server = _build_server_with_tool("read_file")
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": '{"path": "/tmp/example.md"}'},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None, response.error


def test_tools_call_with_unknown_alias_returns_negative_case() -> None:
    server = _build_server_with_tool("read_file")
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={
            "name": f"mcp__{RALPH_MCP_SERVER_NAME}__nonexistent_tool",
            "arguments": {},
        },
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is not None
    assert "is not registered" in cast("str", response.error.get("message", ""))


def test_expose_mcp_aliases_false_disables_alias_emission() -> None:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="read_file",
                description="Test tool read_file",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        cast("Any", _NoopHandler()),
    )
    server = McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=bridge,
        expose_mcp_aliases=False,
    )
    tools = _tools_list(server)
    names = {t["name"] for t in tools}
    assert "read_file" in names
    expected_alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    assert expected_alias not in names
