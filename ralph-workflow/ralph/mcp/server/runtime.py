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
import json
import os
import uuid
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Literal, Protocol, cast

from loguru import logger
from pydantic import BaseModel, ConfigDict, create_model

from ralph import __version__
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
from ralph.mcp.transport.common import mcp_config_as_upstreams, merge_mcp_toml_into_upstreams
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
    load_upstream_tool_catalog,
)
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence

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

    class CreateModelFactoryLike(Protocol):
        """Protocol for pydantic create_model-like factories."""

        def __call__(self, *args: object, **kwargs: object) -> type[BaseModel]:
            """Create a BaseModel subclass."""
            ...

    class ModelConstructLike(Protocol):
        """Protocol for BaseModel.model_construct-like callables."""

        def __call__(self, *args: object, **kwargs: object) -> ToolBuilderLike:
            """Construct a tool instance."""
            ...


_func_metadata_module: ModuleType | None = None
try:
    _fastmcp_module = import_module("mcp.server.fastmcp")
    _tool_module = import_module("mcp.server.fastmcp.tools.base")
    _func_metadata_module = import_module("mcp.server.fastmcp.utilities.func_metadata")
except ModuleNotFoundError:  # pragma: no cover - exercised via runtime fallback tests
    FastMCP = cast("object | None", None)
    Tool = cast("object | None", None)
    _func_metadata_module = None
else:
    FastMCP = cast("object | None", _fastmcp_module.FastMCP)
    Tool = cast("object | None", _tool_module.Tool)

@dataclass(frozen=True)
class McpServerExtras:
    """Optional DI parameters for building standalone MCP servers."""

    session: AgentSession | None = None
    upstream_registry: UpstreamRegistry | None = None
    mcp_config: McpConfig | None = None


FallbackStandaloneServer = _FallbackStandaloneServer


def _make_tool_argument_model(
    *,
    required: set[str],
    property_types: Mapping[str, str | None],
) -> type[BaseModel]:
    """Create a lightweight argument model for FastMCP tool wrappers."""

    fields: dict[str, tuple[type[object], object]] = {}
    for name in property_types:
        fields[name] = (object, ... if name in required else None)
    create_model_factory = cast("CreateModelFactoryLike", create_model)
    return create_model_factory(
        "ToolArgumentModel",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )


def _make_tool_metadata(
    *,
    required: set[str],
    property_types: Mapping[str, str | None],
) -> object:
    """Create FastMCP-compatible metadata without dynamic Pydantic model generation."""

    arg_model = _make_tool_argument_model(required=required, property_types=property_types)

    def pre_parse_json(data: dict[str, object]) -> dict[str, object]:
        new_data = data.copy()
        for data_key, data_value in data.items():
            schema_type = property_types.get(data_key)
            if not isinstance(data_value, str) or schema_type == "string":
                continue
            try:
                loaded: object = json.loads(data_value)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, (str, int, float)):
                continue
            new_data[data_key] = loaded
        return new_data

    async def call_fn_with_arg_validation(
        fn: ToolHandlerLike,
        fn_is_async: bool,
        arguments_to_validate: dict[str, object],
        arguments_to_pass_directly: dict[str, object] | None,
    ) -> object:
        arguments_pre_parsed = pre_parse_json(arguments_to_validate)
        missing = sorted(required.difference(arguments_pre_parsed))
        if missing:
            raise ValueError(f"Missing required tool arguments: {', '.join(missing)}")
        arguments_parsed_model = arg_model.model_validate(arguments_pre_parsed)
        arguments_parsed_dict = cast("dict[str, object]", arguments_parsed_model.model_dump())
        arguments_parsed_dict |= arguments_to_pass_directly or {}
        result = fn(**arguments_parsed_dict)
        if fn_is_async:
            return await cast("Awaitable[object]", result)
        return result

    def convert_result(result: object) -> object:
        if (
            isinstance(result, dict)
            and "content" in result
            and ("isError" in result or "is_error" in result)
        ):
            return result
        if hasattr(result, "isError") and hasattr(result, "content"):
            return result
        _converter: object = getattr(_func_metadata_module, "_convert_to_content", None)
        if callable(_converter):
            return cast("object", _converter(result))
        return result

    return SimpleNamespace(
        arg_model=arg_model,
        output_schema=None,
        output_model=None,
        wrap_output=False,
        pre_parse_json=pre_parse_json,
        call_fn_with_arg_validation=call_fn_with_arg_validation,
        convert_result=convert_result,
    )


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
    allowed_roots = cast("tuple[Path, ...]", getattr(effective_session, "allowed_roots", ()))
    workspace = FsWorkspace(
        workspace_root,
        allowed_roots=allowed_roots if allowed_roots else None,
    )
    mcp_cfg = (
        _extras.mcp_config
        if _extras.mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    tool_catalog = load_upstream_tool_catalog(os.environ.get(UPSTREAM_MCP_TOOL_CATALOG_ENV))
    if tool_catalog:
        upstream_servers = tuple(
            server for server in upstream_servers if server.name in tool_catalog
        )
    if _extras.upstream_registry is not None:
        upstream_reg = _extras.upstream_registry
    elif upstream_servers and tool_catalog:
        upstream_reg = UpstreamRegistry.build_from_tool_catalog(upstream_servers, tool_catalog)
    elif upstream_servers:
        upstream_reg = UpstreamRegistry.build(upstream_servers)
    else:
        upstream_reg = None
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


def _build_tool_handler(registry: ToolBridge, definition: ToolDefinition) -> ToolHandlerLike:
    def _dispatch(**kwargs: object) -> object:
        params = {key: value for key, value in kwargs.items() if value is not None}
        raw_result = registry.dispatch(definition.name, params)
        to_dict = cast("Callable[[], object] | None", getattr(raw_result, "to_dict", None))
        if callable(to_dict):
            return to_dict()
        return raw_result

    def handler(**kwargs: object) -> object:
        return _dispatch(**kwargs)

    handler.__name__ = f"ralph_tool_{definition.name}"
    handler.__doc__ = definition.description
    return handler


def _create_tool(registry: ToolBridge, definition: ToolDefinition) -> ToolBuilderLike:
    schema = definition.input_schema
    properties = cast("dict[str, dict[str, object]]", schema.get("properties", {}))
    required = set(cast("list[str]", schema.get("required", [])))
    property_types = {
        name: cast("str | None", property_schema.get("type"))
        for name, property_schema in properties.items()
    }
    metadata = _make_tool_metadata(required=required, property_types=property_types)
    handler = _build_tool_handler(registry, definition)
    tool_cls = cast("type[BaseModel]", Tool)
    model_construct = cast("ModelConstructLike", cast("object", tool_cls.model_construct))
    return model_construct(
        fn=handler,
        name=definition.name,
        title=None,
        description=definition.description,
        parameters=definition.input_schema,
        fn_metadata=metadata,
        is_async=False,
        context_kwarg=None,
        annotations=None,
        icons=None,
        meta=None,
    )


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
    fastmcp_cls = FastMCP
    tool_cls = Tool
    effective_session = (
        session
        or _extras.session
        or AgentSession(
            session_id=f"standalone-{uuid.uuid4().hex[:8]}",
            run_id=str(uuid.uuid4()),
            drain="standalone",
            capabilities=_all_capability_values(),
        )
    )
    allowed_roots = cast("tuple[Path, ...]", getattr(effective_session, "allowed_roots", ()))
    workspace = FsWorkspace(
        workspace_root,
        allowed_roots=allowed_roots if allowed_roots else None,
    )
    mcp_cfg = (
        _extras.mcp_config
        if _extras.mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    tool_catalog = load_upstream_tool_catalog(os.environ.get(UPSTREAM_MCP_TOOL_CATALOG_ENV))
    if tool_catalog:
        upstream_servers = tuple(
            server for server in upstream_servers if server.name in tool_catalog
        )
    if _extras.upstream_registry is not None:
        upstream_reg = _extras.upstream_registry
    elif upstream_servers and tool_catalog:
        upstream_reg = UpstreamRegistry.build_from_tool_catalog(upstream_servers, tool_catalog)
    elif upstream_servers:
        upstream_reg = UpstreamRegistry.build(upstream_servers)
    else:
        upstream_reg = None
    registry = build_ralph_tool_registry(
        effective_session,
        workspace,
        upstream_registry=upstream_reg,
        mcp_config=mcp_cfg,
    )
    if fastmcp_cls is None or tool_cls is None:
        return cast(
            "FastMcpServerLike",
            FallbackStandaloneServer(host, port, McpServer(effective_session, workspace, registry)),
        )
    tools = cast(
        "list[ToolClass]",
        [_create_tool(registry, definition) for definition in registry.list_definitions()],
    )
    fastmcp_constructor = cast("FastMcpConstructorLike", fastmcp_cls)
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
    return mcp_config_as_upstreams(mcp_config)


def load_runtime_upstream_servers(
    mcp_config: McpConfig,
    env: Mapping[str, str] | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Merge upstream MCP servers from the environment variable and mcp.toml."""
    env_map = os.environ if env is None else env
    raw_upstream = env_map.get(UPSTREAM_MCP_CONFIG_ENV)
    env_servers = load_upstream_mcp_servers(raw_upstream)
    return merge_mcp_toml_into_upstreams(env_servers, _mcp_toml_upstream_servers(mcp_config))


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
    "__version__",
    "build_fastmcp_server",
    "build_standalone_http_server",
    "load_runtime_upstream_servers",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
]
