"""Lightweight MCP server implementation for the fallback HTTP runtime."""

from __future__ import annotations

import base64 as _base64
import json
from typing import TYPE_CHECKING, cast

from ralph import __version__
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.multimodal.resources import parse_media_uri
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._server_state import ServerState

if TYPE_CHECKING:
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.tools.bridge import ToolBridge
    from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from typing import Protocol

    class _ToDict(Protocol):
        def __call__(self) -> dict[str, object]: ...

    class _ModelDump(Protocol):
        def __call__(self, **kwargs: bool) -> dict[str, object]: ...


def _serialize_content_blocks(content_blocks: object) -> list[dict[str, object]]:
    if not isinstance(content_blocks, list | tuple):
        raise TypeError(
            f"content_blocks must be a list or tuple, got {type(content_blocks).__name__}. "
            "Use ToolContent.text_content() or ImageContent() to wrap content."
        )

    serialized: list[dict[str, object]] = []
    blocks = cast("list[object]", content_blocks)
    for idx, block in enumerate(blocks):
        if isinstance(block, dict):
            serialized.append(cast("dict[str, object]", block))
            continue

        to_dict = cast("_ToDict | None", getattr(block, "to_dict", None))
        if callable(to_dict):
            serialized.append(to_dict())
            continue

        model_dump = cast("_ModelDump | None", getattr(block, "model_dump", None))
        if callable(model_dump):
            serialized.append(model_dump(exclude_none=True, by_alias=True))
            continue

        raise TypeError(
            f"Unsupported content block type at index {idx}: "
            f"{type(block).__name__}. "
            "Content blocks must be dict, ToolContent, ImageContent, or a Pydantic model "
            "with to_dict() or model_dump() methods."
        )

    return serialized


def _decode_json_payload_from_content(content_blocks: object) -> dict[str, object] | None:
    serialized = _serialize_content_blocks(content_blocks)
    if not serialized:
        return None
    first = serialized[0]
    text = first.get("text")
    if not isinstance(text, str):
        return None
    try:
        decoded = cast("object", json.loads(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    if "content" not in decoded:
        return None
    return cast("dict[str, object]", decoded)


def _extract_client_capabilities(params: dict[str, object] | None) -> set[str]:
    if not params:
        return set()

    capabilities: object = params.get("capabilities", {})
    if not isinstance(capabilities, dict):
        return set()

    result: set[str] = set()

    for key in capabilities:
        if key in ("image", "media", "multimodal"):
            result.add(key)

    return result


class McpServer:
    """Lightweight MCP server that dispatches JSON-RPC requests to Ralph tools."""

    def __init__(
        self, session: AgentSession, workspace: FsWorkspace, registry: ToolBridge
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = registry
        self._client_capabilities: set[str] | None = None

    def handle_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        if request.method == "notifications/initialized":
            return (None, ServerState.RUNNING)
        if request.method == "tools/call":
            return self._handle_tools_call(request, state)

        handlers = {
            "initialize": self._handle_initialize,
            "prompts/list": self._handle_prompts_list,
            "resources/list": self._handle_resources_list,
            "resources/templates/list": self._handle_resource_templates_list,
            "resources/read": self._handle_resources_read,
            "tools/list": self._handle_tools_list,
        }
        handler = handlers.get(request.method)
        if handler is not None:
            return handler(request)

        error = {"code": -32601, "message": f"Method not found: {request.method}"}
        return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

    def _handle_initialize(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        self._client_capabilities = _extract_client_capabilities(request.params)
        self._registry.set_client_capabilities(self._client_capabilities)

        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "ralph-mcp", "version": __version__},
        }
        return (
            JsonRpcResponse(jsonrpc="2.0", result=result, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_tools_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        tools = [
            {
                "name": definition.name,
                "description": definition.description,
                "inputSchema": definition.input_schema,
            }
            for definition in self._registry.list_definitions()
        ]
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"tools": tools}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_prompts_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"prompts": []}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_resources_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        resources: list[dict[str, object]] = []
        resources.extend(
            entry.resource_list_entry() for entry in self._session.media_manifest.list_entries()
        )
        return (
            JsonRpcResponse(
                jsonrpc="2.0", result={"resources": resources}, msg_id=request.msg_id
            ),
            ServerState.RUNNING,
        )

    def _handle_resource_templates_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        templates: list[dict[str, object]] = []
        if is_policy_approved(self._session.check_capability("media.read")):
            templates.append(
                {
                    "uriTemplate": "ralph://media/{artifact_id}",
                    "name": "Ralph media artifact",
                    "description": (
                        "Binary media artifact stored by read_media. "
                        "Retrieve via resources/read with the full URI."
                    ),
                }
            )
        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"resourceTemplates": templates},
                msg_id=request.msg_id,
            ),
            ServerState.RUNNING,
        )

    def _handle_resources_read(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        params = request.params or {}
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            error = {"code": -32602, "message": "resources/read requires a 'uri' parameter"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        artifact_id = parse_media_uri(uri)
        if artifact_id is None:
            error = {
                "code": -32602,
                "message": (
                    f"Unsupported resource URI: '{uri}'. Expected ralph://media/<artifact_id>"
                ),
            }
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        entry = self._session.media_manifest.get(artifact_id)
        if entry is None:
            error = {"code": -32602, "message": f"Resource not found: '{uri}'"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        raw_bytes = entry.load_bytes()
        if raw_bytes is None:
            error = {"code": -32602, "message": f"Resource bytes no longer available: '{uri}'"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        blob = _base64.b64encode(raw_bytes).decode("ascii")
        contents: list[dict[str, object]] = [
            {"uri": entry.uri, "mimeType": entry.mime_type, "blob": blob},
        ]
        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"contents": contents},
                msg_id=request.msg_id,
            ),
            ServerState.RUNNING,
        )

    def _handle_tools_call(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        params = request.params or {}
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name:
            error = {"code": -32602, "message": "tools/call requires a tool name"}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        arguments_value = params.get("arguments", {})
        if not isinstance(arguments_value, dict):
            error = {"code": -32602, "message": "tools/call arguments must be an object"}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        try:
            raw_result = self._registry.dispatch(
                tool_name, dict(arguments_value), host_session=self._session
            )
        except Exception as exc:
            error = {"code": -32603, "message": str(exc)}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        to_dict = cast("_ToDict | None", getattr(raw_result, "to_dict", None))
        payload_source = to_dict() if callable(to_dict) else raw_result
        payload = self._build_tools_call_payload(payload_source)
        return (
            JsonRpcResponse(jsonrpc="2.0", result=payload, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _build_tools_call_payload(self, payload_source: object) -> dict[str, object]:
        if isinstance(payload_source, dict):
            payload = cast("dict[str, object]", dict(payload_source))
            result_obj = payload.get("result")
            if isinstance(result_obj, dict):
                payload = cast("dict[str, object]", dict(result_obj))
            if "content" not in payload:
                payload["content"] = _serialize_content_blocks(payload_source)
            return payload

        decoded_payload = _decode_json_payload_from_content(payload_source)
        if decoded_payload is not None:
            return decoded_payload
        return {"content": _serialize_content_blocks(payload_source)}
