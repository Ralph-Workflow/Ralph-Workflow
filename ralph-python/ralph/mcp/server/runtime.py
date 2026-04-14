"""Standalone FastMCP HTTP server runtime for Ralph tools."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool

from ralph.mcp.session_bridge import AgentSession
from ralph.mcp.tool_bridge import ToolBridge, ToolDefinition, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_TRANSPORT: Literal["streamable-http"] = "streamable-http"
DEFAULT_MOUNT_PATH = "/mcp"
SESSION_ENV = "RALPH_MCP_SESSION_JSON"
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


class FastMcpServerLike(Protocol):
    """Minimal standalone FastMCP server surface used by Ralph."""

    _tool_manager: ToolManagerLike

    def run(self, transport: Literal["streamable-http"] = DEFAULT_TRANSPORT) -> None:
        """Run the standalone server."""
        ...


def session_from_env() -> AgentSession | None:
    """Load optional session metadata from the environment."""
    raw = os.environ.get(SESSION_ENV)
    if not raw:
        return None
    payload = cast("object", json.loads(raw))
    if not isinstance(payload, dict):
        raise ValueError(f"{SESSION_ENV} must encode an object")

    capabilities_value = payload.get("capabilities", [])
    capabilities = (
        set(cast("list[str]", capabilities_value))
        if isinstance(capabilities_value, list)
        else set()
    )
    return AgentSession(
        session_id=cast("str", payload.get("session_id", f"standalone-{uuid.uuid4().hex[:8]}")),
        run_id=cast("str", payload.get("run_id", str(uuid.uuid4()))),
        drain=cast("str", payload.get("drain", "standalone")),
        capabilities=capabilities,
    )


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
    session: AgentSession | None = None,
) -> FastMcpServerLike:
    """Build a standalone FastMCP server exposing Ralph tools over HTTP."""
    effective_session = session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=set(),
    )
    workspace = FsWorkspace(workspace_root)
    registry = build_ralph_tool_registry(effective_session, workspace)
    tools = cast(
        "list[Tool]",
        [_create_tool(registry, definition) for definition in registry.list_definitions()],
    )
    return cast(
        "FastMcpServerLike",
        FastMCP(
            "ralph-mcp",
            host=host,
            port=port,
            streamable_http_path=DEFAULT_MOUNT_PATH,
            tools=tools,
        ),
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
    "build_fastmcp_server",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
]
