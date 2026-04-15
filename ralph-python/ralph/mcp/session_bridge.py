"""Session bridge connecting Ralph sessions/workspaces to an MCP server."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import socketserver
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

try:
    _FastMCP = cast("object", import_module("mcp.server.fastmcp").FastMCP)
    _TOOL_ERROR_TYPE = cast(
        "type[Exception]", import_module("mcp.server.fastmcp.exceptions").ToolError
    )
except ModuleNotFoundError:  # pragma: no cover - exercised via lazy-import tests
    _FastMCP = None

    class _FallbackToolError(Exception):
        """Fallback MCP tool error when FastMCP is unavailable."""

    _TOOL_ERROR_TYPE = _FallbackToolError


from ralph.mcp.capability_mapping import lookup_ralph_capability
from ralph.mcp.tool_bridge import ToolBridge, ToolDispatchError, build_ralph_tool_registry

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from ralph.workspace import Workspace

_LOGGER = logging.getLogger(__name__)


class _ToDict(Protocol):
    """Callable protocol for MCP tool results that can serialize to a dict."""

    def __call__(self) -> dict[str, object]: ...


class _ModelDump(Protocol):
    """Callable protocol for MCP SDK content blocks exposing model_dump."""

    def __call__(self, *, exclude_none: bool, by_alias: bool) -> dict[str, object]: ...


class _FastMcpLike(Protocol):
    """Minimal FastMCP surface used by the in-process bridge."""

    def add_tool(
        self,
        fn: Callable[[dict[str, object]], object],
        *,
        name: str,
        description: str,
    ) -> None: ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> Coroutine[object, object, object]: ...


class _FallbackFastMCP:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, object]], object]] = {}

    def add_tool(
        self,
        fn: Callable[[dict[str, object]], object],
        *,
        name: str,
        description: str,
    ) -> None:
        del description
        self._handlers[name] = fn

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        handler = self._handlers.get(name)
        if handler is None:
            raise _TOOL_ERROR_TYPE(f"Unknown tool: {name}")
        return handler(arguments)


MCP_ENDPOINT_ENV = "RALPH_MCP_ENDPOINT"
MCP_GENERATION_ENV = "RALPH_MCP_GENERATION"
MCP_RUN_ID_ENV = "RALPH_MCP_RUN_ID"
_STRUCTURED_RESULT_SIZE = 2


@dataclass(frozen=True)
class EndpointLease:
    """Recorded MCP endpoint lease details."""

    endpoint: str
    run_id: str
    drain: str
    generation: int
    ready_at: float


@dataclass(frozen=True)
class ControlCommand:
    """Private orchestrator control command."""

    command: str


@dataclass
class AuditRecord:
    """Single audit entry emitted by the MCP bridge."""

    session_id: str
    timestamp: float
    capability: str
    outcome: str
    message: str


class AuditTrail:
    """Cached audit trail view."""

    def __init__(self, records: list[AuditRecord] | None = None) -> None:
        self._records: list[AuditRecord] = list(records or [])

    def records(self) -> list[AuditRecord]:
        """Return a copy of the recorded entries."""
        return list(self._records)

    def clone(self) -> AuditTrail:
        """Clone the audit trail snapshot."""
        return AuditTrail(self._records)

    @classmethod
    def from_records(cls, records: list[AuditRecord]) -> AuditTrail:
        """Create an audit trail from an explicit record list."""
        return cls(records)

    def extend(self, records: list[AuditRecord]) -> None:
        """Extend the trail with additional records."""
        self._records.extend(records)


class AuditSink:
    """Collector that captures audit records until they are drained."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def emit(self, record: AuditRecord) -> None:
        """Emit an audit record."""
        self._records.append(record)

    def drain_records(self) -> list[AuditRecord]:
        """Return and clear the accumulated records."""
        drained = list(self._records)
        self._records.clear()
        return drained


class ServerState(StrEnum):
    """Simplified server state machine."""

    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


@dataclass
class JsonRpcRequest:
    """Minimal JSON-RPC request model."""

    jsonrpc: str
    method: str
    params: dict[str, object] | None = None
    msg_id: object = None


@dataclass
class JsonRpcResponse:
    """Minimal JSON-RPC response model."""

    jsonrpc: str
    result: object = None
    error: dict[str, object] | None = None
    msg_id: object = None


def _allocate_endpoint_port() -> int:
    """Pick an ephemeral port bound to loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _, port = cast("tuple[str, int]", sock.getsockname())
        return port


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


def endpoint_lease_path(workspace_root: Path) -> Path:
    """Return the path for storing the endpoint lease."""
    return workspace_root.joinpath(".agent", "endpoint_lease.json")


def next_generation_for_run(workspace_root: Path, run_id: str, drain: str) -> int:
    """Compute the next generation counter for the exact run/drain pair."""
    lease_path = endpoint_lease_path(workspace_root)
    if not lease_path.exists():
        return 1
    try:
        payload = cast("dict[str, object]", json.loads(lease_path.read_text()))
    except (json.JSONDecodeError, OSError):
        return 1
    run_id_value = payload.get("run_id")
    drain_value = payload.get("drain")
    if (
        isinstance(run_id_value, str)
        and isinstance(drain_value, str)
        and run_id_value == run_id
        and drain_value == drain
    ):
        generation_value = payload.get("generation")
        if isinstance(generation_value, int):
            generation = generation_value
        elif isinstance(generation_value, str):
            try:
                generation = int(generation_value)
            except ValueError:
                generation = 0
        else:
            generation = 0
        return generation + 1
    return 1


def _workspace_root(workspace: Workspace) -> Path:
    """Determine a filesystem root for the workspace."""
    root = cast("Path | str | None", getattr(workspace, "root", None))
    if isinstance(root, Path):
        return root
    if isinstance(root, str):
        return Path(root)
    return Path()


class McpServer:
    """Simplified in-process MCP server stub."""

    def __init__(self, session: AgentSession, workspace: Workspace, registry: ToolBridge) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = registry
        self._fastmcp = (
            cast("_FastMcpLike", cast("Callable[[str], object]", _FastMCP)("ralph-mcp"))
            if _FastMCP is not None
            else cast("_FastMcpLike", _FallbackFastMCP())
        )
        self._register_tools_with_fastmcp()

    def _register_tools_with_fastmcp(self) -> None:
        for definition in self._registry.list_definitions():
            tool_name = definition.name

            def _make_handler(name: str) -> Callable[[dict[str, object]], object]:
                def _handler(arguments: dict[str, object]) -> object:
                    params = dict(arguments)
                    raw_result = self._registry.dispatch(name, params)
                    to_dict = cast("object", getattr(raw_result, "to_dict", None))
                    return cast("_ToDict", to_dict)() if callable(to_dict) else raw_result

                _handler.__name__ = f"ralph_tool_{name}"
                return _handler

            self._fastmcp.add_tool(
                _make_handler(tool_name),
                name=tool_name,
                description=definition.description,
            )

    def handle_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        """Handle a JSON-RPC request in-process (minimal behavior)."""
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
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"tools": tools},
                msg_id=request.msg_id,
            ),
            ServerState.RUNNING,
        )

    def _handle_tools_call(
        self,
        request: JsonRpcRequest,
        state: ServerState,
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
            call_result = _run_async(
                self._fastmcp.call_tool(tool_name, {"arguments": arguments_value})
            )
        except (ToolDispatchError, _TOOL_ERROR_TYPE, RuntimeError, ValueError) as exc:
            error = {"code": -32603, "message": str(exc)}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        content_blocks: object
        structured_content: object | None = None
        if isinstance(call_result, tuple) and len(call_result) == _STRUCTURED_RESULT_SIZE:
            content_blocks, structured_content = call_result
        else:
            content_blocks = call_result

        payload = self._build_tools_call_payload(content_blocks, structured_content)
        return (
            JsonRpcResponse(jsonrpc="2.0", result=payload, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _build_tools_call_payload(
        self,
        content_blocks: object,
        structured_content: object | None,
    ) -> dict[str, object]:
        if isinstance(structured_content, dict):
            normalized_structured = cast("dict[str, object]", dict(structured_content))
            result_obj = normalized_structured.get("result")
            if isinstance(result_obj, dict):
                payload = cast("dict[str, object]", dict(result_obj))
            else:
                payload = normalized_structured
            if "content" not in payload:
                payload["content"] = _serialize_content_blocks(content_blocks)
            return payload

        decoded_payload = _decode_json_payload_from_content(content_blocks)
        if decoded_payload is not None:
            payload = decoded_payload
        else:
            payload = cast(
                "dict[str, object]",
                {
                    "content": _serialize_content_blocks(content_blocks),
                },
            )
        if structured_content is not None:
            payload["structuredContent"] = structured_content
        return payload


@dataclass
class AgentSession:
    """Lightweight session holder used by Python MCP tooling."""

    session_id: str
    run_id: str
    drain: str
    capabilities: set[str] = field(default_factory=set)
    policy_flags: set[str] | None = None
    created_at: float = field(default_factory=time.time)
    parallel_worker: bool = False
    edit_area_result: object = None

    def check_capability(self, capability: str) -> object:
        """Approve only explicitly granted capabilities or their Ralph aliases."""
        return "approved" if _session_has_capability(self.capabilities, capability) else "denied"

    def is_parallel_worker(self) -> bool:
        """Indicate whether this is a parallel worker session."""
        return self.parallel_worker

    def check_edit_area(self, _: str) -> object:
        """Simplified edit-area check."""
        return self.edit_area_result if self.edit_area_result is not None else "approved"


class McpSessionBridge:
    """Placeholder session bridge that manages server lifecycle."""

    def __init__(self, session: AgentSession, workspace: Workspace) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = build_ralph_tool_registry(session, workspace)
        self._port: int | None = None
        self._started = False
        self._shutdown = False
        self._lease: EndpointLease | None = None
        self._audit_sink: AuditSink | None = None

    def start_with_audit_sink(self, audit_sink: AuditSink, generation: int) -> None:
        """Start the dummy MCP server with an audit sink."""
        if self._started:
            return
        self._port = _allocate_endpoint_port()
        ready_at = time.time()
        self._lease = EndpointLease(
            endpoint=f"tcp://127.0.0.1:{self._port}",
            run_id=self._session.run_id,
            drain=self._session.drain,
            generation=generation,
            ready_at=ready_at,
        )
        self._started = True
        self._shutdown = False
        self._audit_sink = audit_sink

    def endpoint_uri(self) -> str:
        """Return the TCP endpoint URI (connectable to the MCP server)."""
        if self._port is None:
            raise SessionBridgeError("MCP server port is not allocated")
        return f"tcp://127.0.0.1:{self._port}"

    def endpoint_lease(self) -> EndpointLease | None:
        """Return the cached lease if the server was started."""
        return self._lease

    def is_started(self) -> bool:
        """Return whether the dummy server has been started."""
        return self._started

    def is_shutdown(self) -> bool:
        """Return whether the dummy server has been shutdown."""
        return self._shutdown

    def shutdown(self) -> None:
        """Mark the server as shutdown."""
        self._shutdown = True

    def send_control_command(self, command: ControlCommand) -> None:
        """Handle a private control command stub."""
        _LOGGER.debug("Received control command: %s", command.command)

    def build_in_process_server(self) -> McpServer:
        """Create a server instance for deterministic request handling."""
        return McpServer(self._session, self._workspace, self._registry)

    def clone(self) -> McpSessionBridge:
        """Create a shallow clone for repeated bridge views."""
        clone = McpSessionBridge(self._session, self._workspace)
        clone._port = self._port
        clone._started = self._started
        clone._shutdown = self._shutdown
        clone._lease = self._lease
        clone._audit_sink = self._audit_sink
        return clone


def _normalize_capability_token(value: str) -> str:
    return value.strip().replace("-", "_").replace(".", "_").lower()


def _session_has_capability(granted: set[str], requested: str) -> bool:
    normalized_granted = set[str]()
    for value in granted:
        normalized_granted.add(_normalize_capability_token(value))
        mapped_granted = lookup_ralph_capability(value)
        if mapped_granted is not None:
            normalized_granted.add(_normalize_capability_token(mapped_granted.value))

    candidates = {_normalize_capability_token(requested)}
    mapped = lookup_ralph_capability(requested)
    if mapped is not None:
        candidates.add(_normalize_capability_token(mapped.value))
    if requested in {"WorkspaceWriteAny", "FileWrite"}:
        candidates.update({"workspace_write_ephemeral", "workspace_write_tracked"})
    return any(candidate in normalized_granted for candidate in candidates)


class _HttpGatewayHandler(BaseHTTPRequestHandler):
    """Simple HTTP gateway that forwards JSON-RPC payloads."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        if self.path != "/mcp":
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
        jsonrpc_value = data.get("jsonrpc")
        method_value = data.get("method")
        params_value = data.get("params")
        params = (
            cast("dict[str, object] | None", params_value)
            if isinstance(params_value, dict)
            else None
        )
        request = JsonRpcRequest(
            jsonrpc=jsonrpc_value if isinstance(jsonrpc_value, str) else "2.0",
            method=method_value if isinstance(method_value, str) else "",
            params=params,
            msg_id=data.get("id"),
        )
        server_bridge = cast("SessionBridge | None", getattr(self.server, "bridge", None))
        if server_bridge is None:
            self.send_error(500, "Server bridge missing")
            return
        server = server_bridge.build_in_process_server()
        gateway_server = cast("_HttpGatewayServer", self.server)
        response, next_state = server.handle_request(request, gateway_server.state)
        gateway_server.state = next_state
        if response is not None:
            body = {
                "jsonrpc": response.jsonrpc,
                "result": response.result,
                "error": response.error,
                "id": response.msg_id,
            }
        else:
            body = {"jsonrpc": "2.0", "result": None, "id": request.msg_id}
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


class _HttpGatewayServer(socketserver.ThreadingMixIn, HTTPServer):
    bridge: SessionBridge
    state: ServerState

    daemon_threads = True
    allow_reuse_address = True


class SessionBridgeError(Exception):
    """Raised when session bridge operations fail."""


class SessionBridge:
    """Python port of the Rust MCP session bridge."""

    def __init__(self, session: AgentSession, workspace: Workspace) -> None:
        self.session = session
        self.workspace = workspace
        self._inner = McpSessionBridge(session, workspace)
        self.audit_adapter = AuditSink()
        self._cached_audit = AuditTrail()
        self._http_endpoint: str | None = None
        self._http_server: _HttpGatewayServer | None = None
        self._http_thread: threading.Thread | None = None

    def audit_trail(self) -> AuditTrail:
        """Return a snapshot of all audit records."""
        new_records = self.audit_adapter.drain_records()
        if new_records:
            self._cached_audit.extend(new_records)
        return self._cached_audit.clone()

    def drain_audit_records(self) -> list[AuditRecord]:
        """Return only the records emitted since the last drain."""
        new_records = self.audit_adapter.drain_records()
        if new_records:
            self._cached_audit.extend(new_records)
        return new_records

    def endpoint_uri(self) -> str:
        """Return the TCP endpoint that agents connect to."""
        return self._inner.endpoint_uri()

    def agent_endpoint_uri(self) -> str:
        """Return the URI exposed to agents (HTTP gateway when available)."""
        return self._http_endpoint or self.endpoint_uri()

    def endpoint_lease(self) -> EndpointLease | None:
        """Return the latest endpoint lease published by the bridge."""
        lease = self._inner.endpoint_lease()
        if lease is None or self._http_endpoint is None:
            return lease
        return EndpointLease(
            endpoint=self._http_endpoint,
            run_id=lease.run_id,
            drain=lease.drain,
            generation=lease.generation,
            ready_at=lease.ready_at,
        )

    def endpoint_env_var(self) -> str:
        """Return the environment variable name for the MCP endpoint."""
        return MCP_ENDPOINT_ENV

    def is_started(self) -> bool:
        """Return whether the bridge has been started."""
        return self._inner.is_started()

    def is_shutdown(self) -> bool:
        """Return whether the bridge has been shutdown."""
        return self._inner.is_shutdown()

    def start(self) -> None:
        """Start the session bridge and its HTTP gateway."""
        if self.is_started():
            return
        generation = next_generation_for_run(
            _workspace_root(self.workspace),
            self.session.run_id,
            self.session.drain,
        )
        self._inner.start_with_audit_sink(self.audit_adapter, generation)
        self._write_endpoint_lease()
        self._start_http_gateway()

    def shutdown(self) -> None:
        """Shutdown the bridge and release HTTP resources."""
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=1)
            self._http_thread = None
        self._inner.shutdown()

    def send_private_control_command(self, command: ControlCommand) -> None:
        """Send a command over the private control channel."""
        self._inner.send_control_command(command)

    def build_in_process_server(self) -> McpServer:
        """Build an in-process server for deterministic request handling."""
        return self._inner.build_in_process_server()

    def handle_request_in_process(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        """Handle a JSON-RPC request without transport I/O."""
        return self.build_in_process_server().handle_request(request, state)

    def clone(self) -> SessionBridge:
        """Clone the bridge for safe repeated reads."""
        clone = SessionBridge(self.session, self.workspace)
        clone._inner = self._inner.clone()
        clone._cached_audit = self._cached_audit.clone()
        clone._http_endpoint = self._http_endpoint
        clone.audit_adapter = self.audit_adapter
        return clone

    def _write_endpoint_lease(self) -> None:
        lease = self.endpoint_lease()
        if lease is None:
            return
        path = endpoint_lease_path(_workspace_root(self.workspace))
        path.parent.mkdir(parents=True, exist_ok=True)
        lease_dict = cast("dict[str, object]", asdict(lease))
        path.write_text(json.dumps(lease_dict))

    def _start_http_gateway(self) -> None:
        server = _HttpGatewayServer(("127.0.0.1", 0), _HttpGatewayHandler)
        server.bridge = self
        server.state = ServerState.UNINITIALIZED
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._http_server = server
        self._http_thread = thread
        self._http_endpoint = f"http://127.0.0.1:{server.server_address[1]}/mcp"


__all__ = [
    "AgentSession",
    "AuditRecord",
    "AuditSink",
    "AuditTrail",
    "ControlCommand",
    "EndpointLease",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "McpServer",
    "McpSessionBridge",
    "ServerState",
    "SessionBridge",
    "SessionBridgeError",
    "endpoint_lease_path",
    "next_generation_for_run",
]
