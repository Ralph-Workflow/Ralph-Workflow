"""MCP tool registry and handler dispatch.

This module ports the Rust `mcp_server::tool_bridge` registry layer into Python.
It owns tool metadata registration, duplicate protection, lookup, and dispatch.
The default registry builder mirrors the Rust bridge by registering lazy handler
wrappers for the Ralph MCP tool modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

JsonObject = dict[str, object]


class ToolBridgeError(Exception):
    """Base exception for tool bridge failures."""


class ToolRegistrationError(ToolBridgeError):
    """Raised when tool registration is invalid."""


class ToolDispatchError(ToolBridgeError):
    """Raised when tool dispatch fails."""


@dataclass(frozen=True)
class ToolDefinition:
    """Public MCP-facing tool definition."""

    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True)
class ToolMetadata:
    """Internal tool registration metadata."""

    definition: ToolDefinition
    required_capability: str
    is_mutating: bool | None = None


@dataclass(frozen=True)
class ToolSpec:
    """Full registration spec, including lazy import target."""

    metadata: ToolMetadata
    module_name: str
    handler_name: str


@dataclass(frozen=True)
class RegisteredTool:
    """A registered tool and its executable handler."""

    metadata: ToolMetadata
    handler: LazyToolHandler


class LazyToolHandler:
    """Lazy wrapper that imports the real MCP tool handler on demand."""

    def __init__(
        self,
        *,
        module_name: str,
        handler_name: str,
        session: object,
        workspace: object,
    ) -> None:
        self._module_name = module_name
        self._handler_name = handler_name
        self._session = session
        self._workspace = workspace

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        del host_session, workspace
        module = import_module(self._module_name)
        handler = getattr(module, self._handler_name)
        return handler(self._session, self._workspace, params)


class ToolBridge:
    """Registry for MCP tools and dispatcher for tool invocations."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, metadata: ToolMetadata, handler: LazyToolHandler) -> None:
        """Register a tool definition and handler."""
        name = metadata.definition.name
        if name in self._tools:
            raise ToolRegistrationError(f"Tool '{name}' is already registered")
        self._tools[name] = RegisteredTool(metadata=metadata, handler=handler)

    def register_spec(self, spec: ToolSpec, *, session: object, workspace: object) -> None:
        """Register a tool from a complete lazy-loading spec."""
        self.register(
            spec.metadata,
            LazyToolHandler(
                module_name=spec.module_name,
                handler_name=spec.handler_name,
                session=session,
                workspace=workspace,
            ),
        )

    def has_tool(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name in self._tools

    def get(self, name: str) -> RegisteredTool:
        """Return a registered tool or raise if it does not exist."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolDispatchError(f"Tool '{name}' is not registered") from exc

    def list_metadata(self) -> list[ToolMetadata]:
        """Return tool metadata in registration order."""
        return [tool.metadata for tool in self._tools.values()]

    def list_definitions(self) -> list[ToolDefinition]:
        """Return public tool definitions in registration order."""
        return [tool.metadata.definition for tool in self._tools.values()]

    def dispatch(
        self,
        name: str,
        params: JsonObject | None = None,
        *,
        host_session: object | None = None,
        workspace: object | None = None,
    ) -> object:
        """Dispatch a tool invocation to its registered handler."""
        tool = self.get(name)
        tool_params = dict(params or {})
        try:
            return tool.handler(host_session, workspace, tool_params)
        except ToolBridgeError:
            raise
        except Exception as exc:
            raise ToolDispatchError(f"Tool '{name}' failed: {exc}") from exc


def _metadata(
    *,
    name: str,
    description: str,
    input_schema: JsonObject,
    required_capability: str,
) -> ToolMetadata:
    return ToolMetadata(
        definition=ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
        ),
        required_capability=required_capability,
    )


def _tool_specs() -> tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            metadata=_metadata(
                name="read_file",
                description="Read a file from the workspace",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to read"},
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tool_workspace",
            handler_name="handle_read_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name="write_file",
                description="Write content to a file in the workspace",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to write"},
                        "content": {"type": "string", "description": "Content to write"},
                    },
                    "required": ["path", "content"],
                },
                required_capability="WorkspaceWriteAny",
            ),
            module_name="ralph.mcp.tool_workspace",
            handler_name="handle_write_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name="list_directory",
                description="List directory contents",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path to list"},
                        "recursive": {
                            "type": "boolean",
                            "description": "Whether to list recursively",
                            "default": False,
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tool_workspace",
            handler_name="handle_list_directory",
        ),
        ToolSpec(
            metadata=_metadata(
                name="search_files",
                description="Search for files matching a pattern",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Search pattern"},
                        "path": {
                            "type": "string",
                            "description": "Directory path to search in",
                        },
                    },
                    "required": ["pattern", "path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tool_workspace",
            handler_name="handle_search_files",
        ),
        ToolSpec(
            metadata=_metadata(
                name="git_status",
                description="Get git status of the workspace",
                input_schema={"type": "object", "properties": {}},
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tool_git_read",
            handler_name="handle_git_status",
        ),
        ToolSpec(
            metadata=_metadata(
                name="git_diff",
                description="Get git diff of changes",
                input_schema={
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Additional git diff arguments",
                        },
                    },
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tool_git_read",
            handler_name="handle_git_diff",
        ),
        ToolSpec(
            metadata=_metadata(
                name="git_log",
                description="Get git commit log",
                input_schema={
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "number",
                            "description": "Number of commits to show",
                            "default": 10,
                        },
                    },
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tool_git_read",
            handler_name="handle_git_log",
        ),
        ToolSpec(
            metadata=_metadata(
                name="git_show",
                description="Show a git object (commit, tag, etc.)",
                input_schema={
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "Git object reference"},
                    },
                    "required": ["ref"],
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tool_git_read",
            handler_name="handle_git_show",
        ),
        ToolSpec(
            metadata=_metadata(
                name="exec",
                description="Execute a shell command",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute"},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command arguments",
                        },
                        "timeout_ms": {
                            "type": "number",
                            "description": "Timeout in milliseconds",
                            "default": 30000,
                        },
                    },
                    "required": ["command"],
                },
                required_capability="ProcessExecBounded",
            ),
            module_name="ralph.mcp.tool_exec",
            handler_name="handle_exec_command",
        ),
        ToolSpec(
            metadata=_metadata(
                name="ralph_submit_artifact",
                description="Submit a structured artifact",
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "description": (
                                "Type of artifact (plan, development_result, issues, "
                                "fix_result, commit_message)"
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "JSON-serialized artifact payload",
                        },
                        "partial": {
                            "type": "boolean",
                            "description": (
                                "If true, accepts artifact even with validation errors "
                                "(default: false)"
                            ),
                        },
                    },
                    "required": ["artifact_type", "content"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_artifact",
            handler_name="handle_submit_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name="report_progress",
                description="Report progress status to the agent",
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "Status message describing current progress",
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional additional notes or context",
                        },
                    },
                    "required": ["status"],
                },
                required_capability="RunReportProgress",
            ),
            module_name="ralph.mcp.tool_coordination",
            handler_name="handle_report_progress",
        ),
        ToolSpec(
            metadata=_metadata(
                name="declare_complete",
                description="Declare that the agent has completed its task",
                input_schema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Summary of what was accomplished",
                        },
                    },
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_coordination",
            handler_name="handle_declare_complete",
        ),
        ToolSpec(
            metadata=_metadata(
                name="read_env",
                description="Read an environment variable",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Environment variable name",
                        },
                    },
                    "required": ["name"],
                },
                required_capability="EnvRead",
            ),
            module_name="ralph.mcp.tool_coordination",
            handler_name="handle_read_env",
        ),
        ToolSpec(
            metadata=_metadata(
                name="list_directory_recursive",
                description="List directory contents recursively",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path to list recursively",
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tool_workspace",
            handler_name="handle_list_directory_recursive",
        ),
        ToolSpec(
            metadata=_metadata(
                name="coordinate",
                description="Coordinate parallel worker activities",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Coordination action (claim, release, status, ack)",
                        },
                        "work_unit_id": {
                            "type": "string",
                            "description": "Optional work unit identifier",
                        },
                        "payload": {
                            "type": "object",
                            "description": "Optional coordination payload",
                        },
                    },
                    "required": ["action"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_coordination",
            handler_name="handle_coordinate",
        ),
    )


def build_ralph_tool_registry(session: object, workspace: object) -> ToolBridge:
    """Build the default Ralph MCP tool registry.

    This mirrors the Rust `build_ralph_tool_registry` function. The returned
    registry captures `session` and `workspace` so future tool module ports can
    expose Rust-compatible `handle_*` functions without extra adapter glue.
    """

    bridge = ToolBridge()
    for spec in _tool_specs():
        bridge.register_spec(spec, session=session, workspace=workspace)
    return bridge


__all__ = [
    "LazyToolHandler",
    "RegisteredTool",
    "ToolBridge",
    "ToolBridgeError",
    "ToolDefinition",
    "ToolDispatchError",
    "ToolMetadata",
    "ToolRegistrationError",
    "ToolSpec",
    "build_ralph_tool_registry",
]
