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
import os
import uuid
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from loguru import logger

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol.capability_mapping import Capability, McpCapability
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._runtime_constants import (
    DEFAULT_HOST,
    DEFAULT_MOUNT_PATH,
    DEFAULT_PORT,
    DEFAULT_TRANSPORT,
)
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._standalone_http_server import _StandaloneHttpServer
from ralph.mcp.server.runtime_session import FileBackedSession, session_from_env
from ralph.mcp.tools.bridge import ToolBridge, ToolDefinition, build_ralph_tool_registry
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping, Sequence

    from mcp.server.fastmcp.tools.base import Tool as ToolClass

    from ralph.config.mcp_models import McpConfig

if TYPE_CHECKING:
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

    class FastMcpServerLike(Protocol):
        """Minimal standalone FastMCP server surface used by Ralph."""

        _tool_manager: ToolManagerLike

        def run(self, transport: Literal["streamable-http"] = "streamable-http") -> None:
            """Run the standalone server."""
            ...

    class FastMcpConstructorLike(Protocol):
        """Protocol for constructing FastMCP server instances."""

        def __call__(self, *args: object, **kwargs: object) -> FastMcpServerLike:
            """Construct a FastMCP server instance."""
            ...

try:
    _fastmcp_module = import_module("mcp.server.fastmcp")
    _tool_module = import_module("mcp.server.fastmcp.tools.base")
    _FastMCP = cast("object", _fastmcp_module.FastMCP)
    _Tool = cast("object", _tool_module.Tool)
except ModuleNotFoundError:  # pragma: no cover - exercised via runtime fallback tests
    _FastMCP = cast("object | None", None)
    _Tool = cast("object | None", None)

FastMCP = _FastMCP
Tool = _Tool

_SCHEMA_ANNOTATIONS: dict[str, object] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": float,
    "array": list[object],
    "object": dict[str, object],
}


@dataclass(frozen=True)
class McpServerExtras:
    """Optional DI parameters for building standalone MCP servers."""

    session: AgentSession | None = None
    upstream_registry: UpstreamRegistry | None = None
    mcp_config: McpConfig | None = None


FallbackStandaloneServer = _FallbackStandaloneServer


def build_standalone_http_server(
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    extras: McpServerExtras | None = None,
) -> _StandaloneHttpServer:
    """Build a standalone HTTP MCP server backed by the Ralph tool registry."""
    _extras = extras or McpServerExtras()
    effective_session = _extras.session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    workspace = FsWorkspace(workspace_root)
    mcp_cfg = (
        _extras.mcp_config
        if _extras.mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    upstream_reg = _extras.upstream_registry or (
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
    tool_factory = cast("ToolFactoryLike", Tool)
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
    extras: McpServerExtras | None = None,
    session: AgentSession | None = None,
) -> FastMcpServerLike:
    """Build a standalone FastMCP server exposing Ralph tools over HTTP."""
    _extras = extras or McpServerExtras()
    effective_session = session or _extras.session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    workspace = FsWorkspace(workspace_root)
    mcp_cfg = (
        _extras.mcp_config
        if _extras.mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    upstream_reg = _extras.upstream_registry or (
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
    if FastMCP is None or Tool is None:
        return cast(
            "FastMcpServerLike",
            FallbackStandaloneServer(
                host, port, McpServer(effective_session, workspace, registry)
            ),
        )
    tools = cast(
        "list[ToolClass]",
        [_create_tool(registry, definition) for definition in registry.list_definitions()],
    )
    fastmcp_constructor = cast("FastMcpConstructorLike", FastMCP)
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


def load_runtime_upstream_servers(
    mcp_config: McpConfig,
    env: Mapping[str, str] | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Merge upstream MCP servers from the environment variable and mcp.toml."""
    env_map = os.environ if env is None else env
    raw_upstream = env_map.get(UPSTREAM_MCP_CONFIG_ENV)
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
        workspace_root,
        host=host,
        port=port,
        extras=McpServerExtras(session=session_from_env()),
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
    "FileBackedSession",
    "JsonRpcRequest",
    "McpServer",
    "McpServerExtras",
    "ServerState",
    "build_fastmcp_server",
    "build_standalone_http_server",
    "load_runtime_upstream_servers",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
]
