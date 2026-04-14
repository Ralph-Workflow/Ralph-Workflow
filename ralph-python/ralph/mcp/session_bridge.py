"""Session bridge connecting Ralph sessions/workspaces to an MCP server."""

from __future__ import annotations

import json
import logging
import socket
import socketserver
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ralph.mcp.tool_bridge import ToolBridge, build_ralph_tool_registry
from ralph.workspace import Workspace

_LOGGER = logging.getLogger(__name__)

MCP_ENDPOINT_ENV = "RALPH_MCP_ENDPOINT"
MCP_GENERATION_ENV = "RALPH_MCP_GENERATION"
MCP_RUN_ID_ENV = "RALPH_MCP_RUN_ID"


@dataclass(frozen=True)
class EndpointLease:
    """Recorded MCP endpoint lease details."""

    endpoint: str
    run_id: str
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


class ServerState(str, Enum):
    """Simplified server state machine."""

    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


@dataclass
class JsonRpcRequest:
    """Minimal JSON-RPC request model."""

    jsonrpc: str
    method: str
    params: dict[str, Any] | None = None
    msg_id: Any | None = None


@dataclass
class JsonRpcResponse:
    """Minimal JSON-RPC response model."""

    jsonrpc: str
    result: Any | None = None
    error: dict[str, Any] | None = None
    msg_id: Any | None = None


def _allocate_endpoint_port() -> int:
    """Pick an ephemeral port bound to loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        return port


def endpoint_lease_path(workspace_root: Path) -> Path:
    """Return the path for storing the endpoint lease."""
    return workspace_root.joinpath(".agent", "endpoint_lease.json")


def next_generation_for_run(workspace_root: Path, run_id: str) -> int:
    """Compute the next generation counter for the run."""
    lease_path = endpoint_lease_path(workspace_root)
    if not lease_path.exists():
        return 1
    try:
        payload = json.loads(lease_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 1
    if payload.get("run_id") == run_id:
        return int(payload.get("generation", 0)) + 1
    return 1


def _workspace_root(workspace: Workspace) -> Path:
    """Determine a filesystem root for the workspace."""
    root = getattr(workspace, "root", None)
    if isinstance(root, Path):
        return root
    if isinstance(root, str):
        return Path(root)
    return Path(".")


class McpServer:
    """Simplified in-process MCP server stub."""

    def __init__(self, session: AgentSession, workspace: Workspace, registry: ToolBridge) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = registry

    def handle_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        """Handle a JSON-RPC request in-process (minimal behavior)."""
        if request.method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "ralph-mcp"}}
            return (
                JsonRpcResponse(jsonrpc="2.0", result=result, msg_id=request.msg_id),
                ServerState.RUNNING,
            )
        return (None, ServerState.RUNNING)


@dataclass
class AgentSession:
    """Lightweight session holder used by Python MCP tooling."""

    session_id: str
    run_id: str
    drain: str
    capabilities: set[str] | None = None
    policy_flags: set[str] | None = None
    created_at: float = field(default_factory=time.time)
    parallel_worker: bool = False
    edit_area_result: Any | None = None
    _capabilities: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self._capabilities = set(self.capabilities or set())

    def capabilities(self) -> set[str]:
        """Return the granted capability identifiers."""
        return set(self._capabilities)

    def check_capability(self, _: str) -> object:
        """Simplified capability gate that always approves."""
        return "approved"

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
            data = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            self._write_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}, 400)
            return
        request = JsonRpcRequest(
            jsonrpc=data.get("jsonrpc", "2.0"),
            method=data.get("method", ""),
            params=data.get("params"),
            msg_id=data.get("id"),
        )
        server_bridge = getattr(self.server, "bridge", None)
        if server_bridge is None:
            self.send_error(500, "Server bridge missing")
            return
        server = server_bridge.build_in_process_server()
        response, next_state = server.handle_request(request, getattr(self.server, "state", ServerState.UNINITIALIZED))
        self.server.state = next_state
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

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _write_json(self, payload: dict[str, Any], status: int) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _HttpGatewayServer(socketserver.ThreadingMixIn, HTTPServer):
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
        return self._inner.endpoint_lease()

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
        generation = next_generation_for_run(_workspace_root(self.workspace), self.session.run_id)
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
        lease = self._inner.endpoint_lease()
        if lease is None:
            return
        path = endpoint_lease_path(_workspace_root(self.workspace))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(lease)))

    def _start_http_gateway(self) -> None:
        server = _HttpGatewayServer(("127.0.0.1", 0), _HttpGatewayHandler)
        server.bridge = self  # type: ignore[attr-defined]
        server.state = ServerState.UNINITIALIZED  # type: ignore[attr-defined]
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True
        )
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
