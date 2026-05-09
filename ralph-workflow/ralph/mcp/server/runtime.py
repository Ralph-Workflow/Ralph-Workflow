"""Standalone FastMCP HTTP server runtime for Ralph tools.

Runs the Ralph MCP server as a long-lived HTTP process that AI agents connect
to over the MCP protocol. The server exposes Ralph's tool registry (file
operations, git commands, artifact submission, coordination, etc.) through
FastMCP endpoints.

Key responsibilities:

- ``RalphmcpServer`` - the main server class; call ``start(config)`` to launch
  and ``stop()`` to shut down gracefully. A health-check endpoint listens on
  ``/health``; liveness is polled by ``ralph.process.mcp_supervisor``.
- Environment handshake: the server reads ``MCP_SESSION`` (session JSON) and
  ``MCP_SESSION_FILE`` env vars to populate the agent session, which governs
  which capabilities and upstream MCP servers are enabled.
- Tool capability filtering: tools are registered or skipped based on the
  session's declared ``McpCapability`` set so each agent only sees the tools
  it needs.
- Upstream MCP registry: ``load_upstream_mcp_servers`` discovers additional
  MCP servers from ``UPSTREAM_MCP_CONFIG`` and mounts them alongside Ralph
  tools.

The server is launched by ``ralph.process.manager`` via the
``ralph-mcp`` entry point (``ralph/mcp/server/__main__.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from threading import Event
from time import sleep
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

from loguru import logger

from ralph import __version__
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.protocol.capability_mapping import Capability, McpCapability
from ralph.mcp.protocol.env import (
    MCP_SESSION_ENV as SESSION_ENV,
)
from ralph.mcp.protocol.env import (
    MCP_SESSION_FILE_ENV as SESSION_FILE_ENV,
)
from ralph.mcp.protocol.session import AgentSession, session_has_capability
from ralph.mcp.tools.bridge import ToolBridge, ToolDefinition, build_ralph_tool_registry
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from mcp.server.fastmcp.tools.base import Tool as ToolClass

    from ralph.config.mcp_models import McpConfig

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_TRANSPORT: Literal["streamable-http"] = "streamable-http"
DEFAULT_MOUNT_PATH = "/mcp"
SERVER_POLL_INTERVAL_SECONDS = 0.01
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
    def from_function(*args: object, **kwargs: object) -> ToolBuilderLike:
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
    def __call__(self, **kwargs: bool) -> dict[str, object]:
        """Serialize a content block model into a dictionary."""
        ...


class FastMcpServerLike(Protocol):
    """Minimal standalone FastMCP server surface used by Ralph."""

    _tool_manager: ToolManagerLike

    def run(self, transport: Literal["streamable-http"] = DEFAULT_TRANSPORT) -> None:
        """Run the standalone server."""
        ...


class FastMcpConstructorLike(Protocol):
    """Protocol for constructing FastMCP server instances."""

    def __call__(self, *args: object, **kwargs: object) -> FastMcpServerLike:
        """Construct a FastMCP server instance."""
        ...


class ServerState(StrEnum):
    """Lifecycle state of a running MCP server instance."""

    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


@dataclass
class JsonRpcRequest:
    """Parsed representation of an incoming JSON-RPC request."""

    jsonrpc: str
    method: str
    params: dict[str, object] | None = None
    msg_id: object = None


@dataclass
class JsonRpcResponse:
    """Outgoing JSON-RPC response built by McpServer request handlers."""

    jsonrpc: str
    result: object = None
    error: dict[str, object] | None = None
    msg_id: object = None


def _run_async(awaitable: Coroutine[object, object, object]) -> object:
    return asyncio.run(awaitable)


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

        # Check for to_dict() first (ToolContent, ImageContent dataclasses)
        to_dict = cast("_ToDict | None", getattr(block, "to_dict", None))
        if callable(to_dict):
            serialized.append(to_dict())
            continue

        # Check for model_dump() (Pydantic models)
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
    """Extract client capabilities from MCP initialize params.

    The client capabilities can come in various shapes:
    - {"capabilities": {"image": {}, "media": {}}}
    - {"capabilities": {"image": True}}
    - {"clientInfo": {...}}
    """
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

    def __init__(self, session: AgentSession, workspace: FsWorkspace, registry: ToolBridge) -> None:
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

    def _handle_initialize(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
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

    def _handle_prompts_list(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"prompts": []}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_resources_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        resources: list[dict[str, object]] = []
        resources.extend(
            entry.resource_list_entry()
            for entry in self._session.media_manifest.list_entries()
        )
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"resources": resources}, msg_id=request.msg_id),
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
        import base64 as _base64  # noqa: PLC0415

        from ralph.mcp.multimodal.resources import parse_media_uri  # noqa: PLC0415

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
                    f"Unsupported resource URI: '{uri}'. "
                    "Expected ralph://media/<artifact_id>"
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

        blob = _base64.b64encode(entry.raw_bytes).decode("ascii")
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


class _FallbackHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    mcp_server: McpServer
    state: ServerState
    shutdown_event: Event

    def shutdown(self) -> None:
        self.shutdown_event.set()
        super().shutdown()

    def server_close(self) -> None:
        self.shutdown_event.set()
        super().server_close()


class _FallbackHttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path != DEFAULT_MOUNT_PATH:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b"event: open\r\ndata: {}\r\n\r\n")
        self.wfile.flush()
        server = cast("_FallbackHttpServer", self.server)
        while not server.shutdown_event.is_set():
            try:
                self.wfile.write(b": keepalive\r\n\r\n")
                self.wfile.flush()
            except BrokenPipeError:
                break
            sleep(0.25)

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
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = {"jsonrpc": response.jsonrpc, "id": response.msg_id}
        if response.result is not None:
            body["result"] = response.result
        if response.error is not None:
            body["error"] = response.error
        encoded = f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n".encode()
        session_id = None
        if request.method == "initialize":
            session_id = cast("_FallbackHttpServer", self.server).mcp_server._session.session_id
        self._write_sse(encoded, 200, session_id=session_id)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _write_json(self, payload: dict[str, object], status: int) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_sse(self, payload: bytes, status: int, *, session_id: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        if session_id:
            self.send_header("mcp-session-id", session_id)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _FallbackStandaloneServer:
    def __init__(self, host: str, port: int, mcp_server: McpServer) -> None:
        self._host = host
        self._port = port
        self._mcp_server = mcp_server
        self._httpd: _FallbackHttpServer | None = None

    @property
    def bound_address(self) -> tuple[str, int]:
        """Return the (host, port) the server is bound to after run() is called."""
        if self._httpd is None:
            raise RuntimeError("Server has not been started yet")
        return cast("tuple[str, int]", self._httpd.server_address)

    def run(
        self,
        transport: Literal["streamable-http"] = DEFAULT_TRANSPORT,
        *,
        ready_event: Event | None = None,
    ) -> None:
        if transport != DEFAULT_TRANSPORT:
            raise ValueError(f"Unsupported transport: {transport}")
        httpd = _FallbackHttpServer((self._host, self._port), _FallbackHttpHandler)
        httpd.mcp_server = self._mcp_server
        httpd.state = ServerState.UNINITIALIZED
        httpd.shutdown_event = Event()
        self._httpd = httpd
        if ready_event is not None:
            ready_event.set()
        httpd.serve_forever(poll_interval=SERVER_POLL_INTERVAL_SECONDS)


class _StandaloneHttpServer(_FallbackStandaloneServer):
    pass


def build_standalone_http_server(  # noqa: PLR0913
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    session: AgentSession | None = None,
    upstream_registry: UpstreamRegistry | None = None,
    mcp_config: McpConfig | None = None,
) -> _StandaloneHttpServer:
    """Build a standalone HTTP MCP server backed by the Ralph tool registry."""
    effective_session = session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    workspace = FsWorkspace(workspace_root)
    mcp_cfg = (
        mcp_config
        if mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = _load_runtime_upstream_servers(mcp_cfg)
    upstream_reg = (
        UpstreamRegistry.build(upstream_servers, on_unreachable="warn_and_skip")
        if upstream_servers
        else None
    )
    registry = build_ralph_tool_registry(
        effective_session,
        workspace,
        upstream_registry=upstream_reg,
        mcp_config=mcp_cfg,
    )
    n_builtin = len(list(registry.list_definitions()))
    if upstream_reg and upstream_servers:
        n_proxied = len(list(upstream_reg.tool_definitions()))
        n_servers = len(upstream_servers)
        logger.info(
            "MCP server started with {n} built-in tools + "
            "{m} proxied upstream tools from {k} servers",
            n=n_builtin,
            m=n_proxied,
            k=n_servers,
        )
    else:
        logger.info("MCP server started with {n} built-in tools", n=n_builtin)
    return _StandaloneHttpServer(host, port, McpServer(effective_session, workspace, registry))


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
        from ralph.mcp.multimodal.resources import MediaManifest  # noqa: PLC0415
        self._media_manifest = MediaManifest()


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

    @property
    def worker_artifact_dir(self) -> Path | None:
        """Return worker artifact dir from environment variable.

        For parallel workers, the parent process sets RALPH_WORKER_ARTIFACT_DIR
        in the subprocess environment. This property reads that value so that
        artifact submission can route to the correct per-worker namespace.
        """
        raw = os.environ.get("RALPH_WORKER_ARTIFACT_DIR")
        if raw is None:
            return None
        return Path(raw)

    @property
    def model_identity(self) -> object:
        from ralph.mcp.multimodal.capabilities import (  # noqa: PLC0415
            UNKNOWN_IDENTITY,
            MultimodalModelIdentity,
        )
        raw = self._load().get("model_identity")
        if not isinstance(raw, dict):
            return UNKNOWN_IDENTITY
        provider = str(raw.get("provider", "unknown"))
        model_id = raw.get("model_id")
        transport = raw.get("transport")
        return MultimodalModelIdentity(
            provider=provider,
            model_id=str(model_id) if model_id is not None else None,
            transport=str(transport) if transport is not None else None,
        )

    @property
    def capability_profile(self) -> object:
        from ralph.mcp.multimodal.capabilities import (  # noqa: PLC0415
            UNKNOWN_IDENTITY,
            MultimodalModelIdentity,
            profile_from_payload,
            resolve_capability_profile,
        )
        raw = self._load().get("capability_profile")
        if isinstance(raw, dict):
            return profile_from_payload(raw)
        identity = self.model_identity
        if not isinstance(identity, MultimodalModelIdentity):
            return resolve_capability_profile(UNKNOWN_IDENTITY)
        return resolve_capability_profile(identity)

    @property
    def media_manifest(self) -> object:
        return self._media_manifest

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
    from ralph.mcp.multimodal.capabilities import (  # noqa: PLC0415
        UNKNOWN_IDENTITY,
        MultimodalModelIdentity,
        profile_from_payload,
        resolve_capability_profile,
    )
    raw_identity = payload.get("model_identity")
    if isinstance(raw_identity, dict):
        provider = str(raw_identity.get("provider", "unknown"))
        model_id_raw = raw_identity.get("model_id")
        transport_raw = raw_identity.get("transport")
        model_identity = MultimodalModelIdentity(
            provider=provider,
            model_id=str(model_id_raw) if model_id_raw is not None else None,
            transport=str(transport_raw) if transport_raw is not None else None,
        )
    else:
        model_identity = UNKNOWN_IDENTITY
    raw_profile = payload.get("capability_profile")
    stored_profile = profile_from_payload(raw_profile) if isinstance(raw_profile, dict) else None
    if stored_profile is None and model_identity.is_known():
        stored_profile = resolve_capability_profile(model_identity)
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
        model_identity=model_identity,
        stored_capability_profile=stored_profile,
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


def build_fastmcp_server(  # noqa: PLR0913
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    session: AgentSession | None = None,
    upstream_registry: UpstreamRegistry | None = None,
    mcp_config: McpConfig | None = None,
) -> FastMcpServerLike:
    """Build a standalone FastMCP server exposing Ralph tools over HTTP."""
    effective_session = session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    workspace = FsWorkspace(workspace_root)
    mcp_cfg = (
        mcp_config
        if mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = _load_runtime_upstream_servers(mcp_cfg)
    upstream_reg = (
        UpstreamRegistry.build(upstream_servers, on_unreachable="warn_and_skip")
        if upstream_servers
        else None
    )
    registry = build_ralph_tool_registry(
        effective_session,
        workspace,
        upstream_registry=upstream_reg,
        mcp_config=mcp_cfg,
    )
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


def _workspace_mcp_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "mcp.toml"


def _mcp_toml_upstream_servers(mcp_config: McpConfig) -> tuple[UpstreamMcpServer, ...]:
    return tuple(
        UpstreamMcpServer(
            name=spec.name,
            transport=spec.transport,
            url=spec.url,
            command=spec.command,
            args=tuple(spec.args),
            env=dict(spec.env),
        )
        for spec in mcp_config.mcp_servers.values()
    )


def _load_runtime_upstream_servers(mcp_config: McpConfig) -> tuple[UpstreamMcpServer, ...]:
    raw_upstream = os.environ.get(UPSTREAM_MCP_CONFIG_ENV)
    env_servers = load_upstream_mcp_servers(raw_upstream)
    merged: dict[str, UpstreamMcpServer] = {server.name: server for server in env_servers}
    for server in _mcp_toml_upstream_servers(mcp_config):
        merged[server.name] = server
    return tuple(merged.values())


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

    server = build_standalone_http_server(
        workspace_root, host=host, port=port, session=session_from_env()
    )
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
    "build_standalone_http_server",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
]
