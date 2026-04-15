"""Standalone FastMCP HTTP server runtime for Ralph tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    _fastmcp_module = import_module("mcp.server.fastmcp")
    _tool_module = import_module("mcp.server.fastmcp.tools.base")
    _FastMCP = cast("object", _fastmcp_module.FastMCP)
    _Tool = cast("object", _tool_module.Tool)
except ModuleNotFoundError:  # pragma: no cover - exercised via runtime fallback tests
    _FastMCP = cast("object | None", None)
    _Tool = cast("object | None", None)

from ralph.mcp.capability_mapping import Capability, McpCapability
from ralph.mcp.env import (
    MCP_SESSION_ENV as SESSION_ENV,
)
from ralph.mcp.env import (
    MCP_SESSION_FILE_ENV as SESSION_FILE_ENV,
)
from ralph.mcp.session import AgentSession, session_has_capability
from ralph.mcp.tool_bridge import ToolBridge, ToolDefinition, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from mcp.server.fastmcp.tools.base import Tool as ToolClass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_TRANSPORT: Literal["streamable-http"] = "streamable-http"
DEFAULT_MOUNT_PATH = "/mcp"
_SCHEMA_ANNOTATIONS: dict[str, object] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": float,
    "array": list[object],
    "object": dict[str, object],
}


class RegisteredToolLike(Protocol):
    """Minimal registered FastMCP tool surface used by tests."""

    name: str
    parameters: dict[str, object]


class ToolManagerLike(Protocol):
    """Minimal FastMCP tool manager surface used by tests."""

    def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> Coroutine[object, object, object]:
        """Call a registered tool."""
        ...

    def list_tools(self) -> list[RegisteredToolLike]:
        """Return registered tools."""
        ...


class ToolBuilderLike(Protocol):
    """Mutable tool surface returned by FastMCP tool registration."""

    parameters: dict[str, object]


class ToolFactoryLike(Protocol):
    """Factory surface used to create typed FastMCP tools."""

    @staticmethod
    def from_function(
        fn: object,
        *,
        name: str,
        description: str,
        structured_output: bool,
    ) -> ToolBuilderLike:
        """Create a tool from a callable."""
        ...


class ToolHandlerLike(Protocol):
    """Dynamic callable wrapper accepted by the FastMCP tool factory."""

    __name__: str
    __doc__: str | None

    def __call__(self, **kwargs: object) -> object:
        """Invoke the wrapped Ralph tool."""
        ...


class _ToDict(Protocol):
    """Callable protocol for tool results that can serialize to a dict."""

    def __call__(self) -> dict[str, object]:
        """Serialize result to a dictionary."""
        ...


class _ModelDump(Protocol):
    def __call__(self, *, exclude_none: bool, by_alias: bool) -> dict[str, object]:
        """Serialize a content block model into a dictionary."""
        ...


class FastMcpServerLike(Protocol):
    """Minimal standalone FastMCP server surface used by Ralph."""

    _tool_manager: ToolManagerLike

    def run(self, transport: Literal["streamable-http"] = DEFAULT_TRANSPORT) -> None:
        """Run the standalone server."""
        ...


class FastMcpConstructorLike(Protocol):
    def __call__(
        self,
        name: str,
        *,
        host: str,
        port: int,
        streamable_http_path: str,
        tools: list[ToolClass],
    ) -> FastMcpServerLike:
        """Construct a FastMCP server instance."""
        ...


class ServerState(StrEnum):
    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


@dataclass
class JsonRpcRequest:
    jsonrpc: str
    method: str
    params: dict[str, object] | None = None
    msg_id: object = None


@dataclass
class JsonRpcResponse:
    jsonrpc: str
    result: object = None
    error: dict[str, object] | None = None
    msg_id: object = None


def _run_async(awaitable: Coroutine[object, object, object]) -> object:
    return asyncio.run(awaitable)


def _serialize_content_blocks(content_blocks: object) -> list[dict[str, object]]:
    if not isinstance(content_blocks, list | tuple):
        return [{"type": "text", "text": str(content_blocks)}]

    serialized: list[dict[str, object]] = []
    for block in content_blocks:
        if isinstance(block, dict):
            serialized.append(cast("dict[str, object]", block))
            continue

        model_dump = cast("_ModelDump | None", getattr(block, "model_dump", None))
        if callable(model_dump):
            serialized.append(model_dump(exclude_none=True, by_alias=True))
            continue

        serialized.append({"type": "text", "text": str(block)})

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


class McpServer:
    def __init__(self, session: AgentSession, workspace: FsWorkspace, registry: ToolBridge) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = registry

    def handle_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        if request.method == "initialize":
            return self._handle_initialize(request)
        if request.method == "notifications/initialized":
            return (None, ServerState.RUNNING)
        if request.method == "tools/list":
            return self._handle_tools_list(request)
        if request.method == "tools/call":
            return self._handle_tools_call(request, state)

        error = {"code": -32601, "message": f"Method not found: {request.method}"}
        return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

    def _handle_initialize(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ralph-mcp"},
        }
        return (
            JsonRpcResponse(jsonrpc="2.0", result=result, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_tools_list(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
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
            raw_result = self._registry.dispatch(tool_name, dict(arguments_value))
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


class _FallbackHttpServer(HTTPServer):
    mcp_server: McpServer
    state: ServerState


class _FallbackHttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        if self.path != DEFAULT_MOUNT_PATH:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        try:
            data = cast("dict[str, object]", json.loads(payload or b"{}"))
        except json.JSONDecodeError:
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                    "id": None,
                },
                400,
            )
            return
        params_value = data.get("params")
        request = JsonRpcRequest(
            jsonrpc=cast("str", data.get("jsonrpc", "2.0")),
            method=cast("str", data.get("method", "")),
            params=cast("dict[str, object] | None", params_value)
            if isinstance(params_value, dict)
            else None,
            msg_id=data.get("id"),
        )
        server = cast("_FallbackHttpServer", self.server)
        response, next_state = server.mcp_server.handle_request(request, server.state)
        server.state = next_state
        body = (
            {
                "jsonrpc": response.jsonrpc,
                "result": response.result,
                "error": response.error,
                "id": response.msg_id,
            }
            if response is not None
            else {"jsonrpc": "2.0", "result": None, "id": request.msg_id}
        )
        self._write_json(body, 200)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _write_json(self, payload: dict[str, object], status: int) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _FallbackStandaloneServer:
    def __init__(self, host: str, port: int, mcp_server: McpServer) -> None:
        self._host = host
        self._port = port
        self._mcp_server = mcp_server
        self._httpd: _FallbackHttpServer | None = None

    def run(self, transport: Literal["streamable-http"] = DEFAULT_TRANSPORT) -> None:
        if transport != DEFAULT_TRANSPORT:
            raise ValueError(f"Unsupported transport: {transport}")
        httpd = _FallbackHttpServer((self._host, self._port), _FallbackHttpHandler)
        httpd.mcp_server = self._mcp_server
        httpd.state = ServerState.UNINITIALIZED
        self._httpd = httpd
        httpd.serve_forever()


class FileBackedSession:
    """Session view backed by a JSON file updated by the parent Ralph process."""

    def __init__(
        self,
        path: Path,
        *,
        loader: Callable[[Path], dict[str, object]] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._path = path
        self._loader = loader or _load_session_payload
        self._session_id_factory = session_id_factory or (
            lambda: f"standalone-{uuid.uuid4().hex[:8]}"
        )
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))

    def _load(self) -> dict[str, object]:
        return self._loader(self._path)

    @property
    def session_id(self) -> str:
        return cast("str", self._load().get("session_id", self._session_id_factory()))

    @property
    def run_id(self) -> str:
        return cast("str", self._load().get("run_id", self._run_id_factory()))

    @property
    def drain(self) -> str:
        return cast("str", self._load().get("drain", "standalone"))

    @property
    def capabilities(self) -> set[str]:
        capabilities_value: object = self._load().get("capabilities", [])
        if not isinstance(capabilities_value, list):
            return set()
        return set(cast("list[str]", capabilities_value))

    def check_capability(self, capability: str) -> object:
        return "approved" if session_has_capability(self.capabilities, capability) else "denied"

    def is_parallel_worker(self) -> bool:
        return False

    def check_edit_area(self, _: str) -> object:
        return "approved"


def _load_session_payload(path: Path) -> dict[str, object]:
    payload = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must encode an object")
    return cast("dict[str, object]", payload)


def session_from_env(
    env: dict[str, str] | os._Environ[str] | None = None,
    *,
    session_id_factory: Callable[[], str] | None = None,
    run_id_factory: Callable[[], str] | None = None,
) -> AgentSession | None:
    """Load optional session metadata from the environment."""
    env_map = os.environ if env is None else env
    session_file = env_map.get(SESSION_FILE_ENV)
    if session_file:
        return cast(
            "AgentSession",
            FileBackedSession(
                Path(session_file),
                session_id_factory=session_id_factory,
                run_id_factory=run_id_factory,
            ),
        )

    raw = env_map.get(SESSION_ENV)
    if not raw:
        return None
    payload = cast("object", json.loads(raw))
    if not isinstance(payload, dict):
        raise ValueError(f"{SESSION_ENV} must encode an object")

    capabilities_value: object = payload.get("capabilities", [])
    capabilities = (
        set(cast("list[str]", capabilities_value))
        if isinstance(capabilities_value, list)
        else set()
    )
    return AgentSession(
        session_id=cast(
            "str",
            payload.get(
                "session_id",
                session_id_factory()
                if session_id_factory is not None
                else f"standalone-{uuid.uuid4().hex[:8]}",
            ),
        ),
        run_id=cast(
            "str",
            payload.get(
                "run_id",
                run_id_factory() if run_id_factory is not None else str(uuid.uuid4()),
            ),
        ),
        drain=cast("str", payload.get("drain", "standalone")),
        capabilities=capabilities,
    )


def _all_capability_values() -> set[str]:
    values = {cap.value for cap in Capability}
    values.update(cap.value for cap in McpCapability)
    return values


def _annotation_for_schema(schema: object) -> object:
    if not isinstance(schema, dict):
        return object

    schema_type = cast("str | None", schema.get("type"))
    return _SCHEMA_ANNOTATIONS.get(schema_type or "", object)


def _tool_signature_parts(definition: ToolDefinition) -> tuple[list[str], dict[str, object]]:
    schema = definition.input_schema
    properties = cast("dict[str, dict[str, object]]", schema.get("properties", {}))
    required = set(cast("list[str]", schema.get("required", [])))
    parameter_parts: list[str] = []
    annotations: dict[str, object] = {"return": object}

    for name, property_schema in properties.items():
        if not name.isidentifier():
            raise ValueError(f"Unsupported MCP parameter name: {name}")
        annotations[name] = _annotation_for_schema(property_schema)
        if name in required:
            parameter_parts.append(f"{name}: __annotations__[{name!r}]")
        else:
            parameter_parts.append(f"{name}: __annotations__[{name!r}] = None")

    return parameter_parts, annotations


def _build_tool_handler(registry: ToolBridge, definition: ToolDefinition) -> ToolHandlerLike:
    parameter_parts, annotations = _tool_signature_parts(definition)

    def _dispatch(**kwargs: object) -> object:
        params = {key: value for key, value in kwargs.items() if value is not None}
        raw_result = registry.dispatch(definition.name, params)
        to_dict = cast("_ToDict | None", getattr(raw_result, "to_dict", None))
        return to_dict() if callable(to_dict) else raw_result

    params_src = ", ".join(parameter_parts)
    call_args = ", ".join(f"{name}={name}" for name in annotations if name != "return")
    signature_src = f"*, {params_src}" if params_src else ""
    function_src = f"def handler({signature_src}):\n    return _dispatch({call_args})\n"
    namespace = {
        "_dispatch": _dispatch,
        "__annotations__": annotations,
    }
    exec(function_src, namespace)
    handler = cast("ToolHandlerLike", namespace["handler"])
    handler.__name__ = f"ralph_tool_{definition.name}"
    handler.__doc__ = definition.description
    return handler


def _create_tool(registry: ToolBridge, definition: ToolDefinition) -> ToolBuilderLike:
    tool_factory = cast("ToolFactoryLike", _Tool)
    tool = tool_factory.from_function(
        _build_tool_handler(registry, definition),
        name=definition.name,
        description=definition.description,
        structured_output=False,
    )
    tool.parameters = definition.input_schema
    return tool


def build_fastmcp_server(
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    session: AgentSession | None = None,
) -> FastMcpServerLike:
    """Build a standalone FastMCP server exposing Ralph tools over HTTP."""
    effective_session = session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    workspace = FsWorkspace(workspace_root)
    registry = build_ralph_tool_registry(effective_session, workspace)
    if _FastMCP is None or _Tool is None:
        return cast(
            "FastMcpServerLike",
            _FallbackStandaloneServer(
                host, port, McpServer(effective_session, workspace, registry)
            ),
        )
    tools = cast(
        "list[ToolClass]",
        [_create_tool(registry, definition) for definition in registry.list_definitions()],
    )
    fastmcp_constructor = cast("FastMcpConstructorLike", _FastMCP)
    return fastmcp_constructor(
        "ralph-mcp",
        host=host,
        port=port,
        streamable_http_path=DEFAULT_MOUNT_PATH,
        tools=tools,
    )


def run_standalone_server(
    workspace_root: Path,
    *,
    transport: str = DEFAULT_TRANSPORT,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the standalone Ralph MCP server over HTTP."""
    if transport != DEFAULT_TRANSPORT:
        raise ValueError(f"Unsupported transport: {transport}")

    server = build_fastmcp_server(workspace_root, host=host, port=port, session=session_from_env())
    print(f"Ralph MCP server listening on http://{host}:{port}{DEFAULT_MOUNT_PATH}")
    server.run(transport=DEFAULT_TRANSPORT)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse standalone MCP server CLI arguments."""
    parser = argparse.ArgumentParser(description="Run the standalone Ralph MCP HTTP server")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root exposed to Ralph MCP tools",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP bind port")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for the standalone Ralph MCP HTTP server."""
    args = parse_args(argv)
    run_standalone_server(
        cast("Path", args.workspace),
        transport=DEFAULT_TRANSPORT,
        host=cast("str", args.host),
        port=cast("int", args.port),
    )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MOUNT_PATH",
    "DEFAULT_PORT",
    "DEFAULT_TRANSPORT",
    "SESSION_ENV",
    "SESSION_FILE_ENV",
    "FileBackedSession",
    "build_fastmcp_server",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
]
