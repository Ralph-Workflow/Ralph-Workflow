"""Regression tests for client-flavored tool-schema advertisement.

The live failure mode (kimi-code 0.36.1, Moonshot API, verified on the
wire 2026-08-16): the Kimi Code CLI forwards every MCP ``tools/list``
entry verbatim as OpenAI-style ``tools[].function.parameters``, and
Moonshot rejects a ROOT schema that mixes ``type`` with composition
keywords::

    400 tools.function.parameters is not a valid moonshot flavored json
    schema, details: <At path 'root': when using anyOf, type should be
    defined in anyOf items instead of the parent schema>

Ralph advertises three composed roots (``read_file``,
``read_multiple_files``, ``exec``). The server negotiates the schema
flavor at the MCP ``initialize`` handshake and flattens ONLY the
advertised root for clients that need the OpenAI function flavor,
leaving the registered ``ToolDefinition.input_schema`` and every other
client's advertisement untouched.
"""

from __future__ import annotations

from typing import Any

from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._schema_flavor import (
    OPENAI_FUNCTION_FLAVOR,
    flatten_root_schema_for_openai_function,
    schema_flavor_for_client_name,
)
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, claude_tool_name
from tests._support.typed_accessors import must_mapping


class _NoopHandler:
    def __call__(
        self, session: object, workspace: object, params: dict[str, object]
    ) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}


#: Root schema mirroring the shipped ``read_file`` contract: a top-level
#: ``oneOf`` selector split plus ``allOf``/``not`` mutual-exclusion pairs —
#: exactly the shapes Moonshot's OpenAI-function flavor rejects.
_COMPOSED_ROOT_SCHEMA: dict[str, object] = {
    "type": "object",
    "allOf": [{"not": {"required": ["line_start", "offset"]}}],
    "oneOf": [
        {
            "properties": {"evidence_id": False, "span_id": False, "symbol": False},
            "required": ["path"],
            "title": "Path selector",
        },
        {
            "properties": {"path": False, "span_id": False, "symbol": False},
            "required": ["evidence_id"],
            "title": "Evidence selector",
        },
    ],
    "properties": {
        "path": {"type": "string", "description": "File path."},
        "evidence_id": {"type": "string", "description": "Indexed evidence handle."},
        "command": {
            "description": "String or argv array.",
            "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
        },
    },
    "required": ["path"],
    "additionalProperties": False,
    "description": "Composed root fixture.",
}


def _build_server_with_composed_tool(name: str = "read_file") -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name=name,
                description=f"Test tool {name}",
                input_schema=dict(_COMPOSED_ROOT_SCHEMA),
            ),
            required_capability="workspace.read",
        ),
        _NoopHandler(),
    )
    return McpServer(
        session=object(),
        workspace=object(),
        registry=bridge,
    )


def _initialize(server: McpServer, client_name: str | None) -> None:
    params: dict[str, object] = {}
    if client_name is not None:
        params["clientInfo"] = {"name": client_name, "version": "0.0.0"}
    request = JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id="0", params=params)
    server._handle_initialize(request)


def _tools_list(server: McpServer) -> list[dict[str, object]]:
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})
    response, _ = server._handle_tools_list(request)
    assert response.result is not None
    return [dict(tool) for tool in response.result["tools"]]


def _schema_of(entry: dict[str, object]) -> dict[str, object]:
    return dict(must_mapping(entry["inputSchema"]))


def test_schema_flavor_mapping() -> None:
    assert schema_flavor_for_client_name("kimi-code") == OPENAI_FUNCTION_FLAVOR
    assert schema_flavor_for_client_name("claude-code") is None
    assert schema_flavor_for_client_name("") is None
    assert schema_flavor_for_client_name(None) is None


def test_flatten_root_schema_drops_composition_and_keeps_plain_vocabulary() -> None:
    flattened = flatten_root_schema_for_openai_function(_COMPOSED_ROOT_SCHEMA)
    assert set(flattened) == {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "description",
    }
    assert flattened["type"] == "object"
    assert flattened["properties"] == _COMPOSED_ROOT_SCHEMA["properties"]
    assert flattened["required"] == ["path"]
    # Nested property sub-schemas keep their own vocabulary (property-level
    # ``oneOf`` passed Moonshot's validator in the live 43-tool run).
    command = dict(must_mapping(flattened["properties"]["command"]))
    assert "oneOf" in command


def test_flatten_root_schema_defaults_type_and_is_idempotent() -> None:
    untyped_root: dict[str, object] = {
        "anyOf": [{"required": ["command"]}, {"required": ["argv"]}],
        "properties": {"command": {"type": "string"}},
    }
    once = flatten_root_schema_for_openai_function(untyped_root)
    assert once["type"] == "object"
    assert "anyOf" not in once
    twice = flatten_root_schema_for_openai_function(once)
    assert twice == once


def test_flatten_root_schema_does_not_mutate_input() -> None:
    original = dict(_COMPOSED_ROOT_SCHEMA)
    flatten_root_schema_for_openai_function(original)
    assert original == _COMPOSED_ROOT_SCHEMA


def test_kimi_code_handshake_flattens_raw_and_alias_advertisement() -> None:
    server = _build_server_with_composed_tool()
    _initialize(server, "kimi-code")
    tools = _tools_list(server)
    alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    raw = next(tool for tool in tools if tool["name"] == "read_file")
    alias_entry = next(tool for tool in tools if tool["name"] == alias)
    for entry in (raw, alias_entry):
        schema = _schema_of(entry)
        assert set(schema) == {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "description",
        }
        assert schema["type"] == "object"


def test_unknown_client_handshake_keeps_full_json_schema() -> None:
    server = _build_server_with_composed_tool()
    _initialize(server, "claude-code")
    tools = _tools_list(server)
    raw = next(tool for tool in tools if tool["name"] == "read_file")
    assert _schema_of(raw) == _COMPOSED_ROOT_SCHEMA


def test_re_handshake_without_client_info_resets_flavor() -> None:
    """A later handshake from a client that does not identify itself (e.g.
    Ralph's own preflight probe against a restarted server) must restore the
    default full-schema advertisement rather than sticking on the earlier
    client's flavor."""
    server = _build_server_with_composed_tool()
    _initialize(server, "kimi-code")
    _initialize(server, None)
    tools = _tools_list(server)
    raw = next(tool for tool in tools if tool["name"] == "read_file")
    assert _schema_of(raw) == _COMPOSED_ROOT_SCHEMA


def test_flattened_advertisement_leaves_registered_definition_untouched() -> None:
    server = _build_server_with_composed_tool()
    _initialize(server, "kimi-code")
    _tools_list(server)
    definition = server._registry.list_definitions()[0]
    assert definition.input_schema == _COMPOSED_ROOT_SCHEMA


def test_tools_call_dispatches_after_flattened_advertisement() -> None:
    server = _build_server_with_composed_tool()
    _initialize(server, "kimi-code")
    alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="2",
        params={"name": alias, "arguments": {"path": "/tmp/example.md"}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None, response.error
