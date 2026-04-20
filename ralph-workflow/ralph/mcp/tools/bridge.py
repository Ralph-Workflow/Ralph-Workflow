"""MCP tool registry and handler dispatch.

This module ports the Rust `mcp_server::tool_bridge` registry layer into Python.
It owns tool metadata registration, duplicate protection, lookup, and dispatch.
The default registry builder mirrors the Rust bridge by registering lazy handler
wrappers for the Ralph MCP tool modules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.protocol.capability_mapping import McpCapability
from ralph.mcp.tools.names import (
    COORDINATE_TOOL,
    DECLARE_COMPLETE_TOOL,
    DIRECTORY_TREE_TOOL,
    DISCARD_PLAN_DRAFT_TOOL,
    EXEC_TOOL,
    FINALIZE_PLAN_TOOL,
    GET_PLAN_DRAFT_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    LIST_DIRECTORY_TOOL,
    READ_ENV_TOOL,
    READ_FILE_TOOL,
    REPORT_PROGRESS_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_PLAN_SECTION_TOOL,
    WEB_SEARCH_TOOL,
    WRITE_FILE_TOOL,
)

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.upstream.registry import UpstreamRegistry

JsonObject = dict[str, object]
ToolHandler = Callable[[object, object, JsonObject], object]


class RegistrationHandler(Protocol):
    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object: ...


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
    handler: RegistrationHandler


class LazyToolHandler:
    """Lazy wrapper that imports the real MCP tool handler on demand."""

    def __init__(
        self,
        *,
        module_name: str,
        handler_name: str,
        session: object,
        workspace: object,
        extra_kwargs: dict[str, object] | None = None,
    ) -> None:
        self._module_name = module_name
        self._handler_name = handler_name
        self._session = session
        self._workspace = workspace
        self._extra_kwargs: dict[str, object] = extra_kwargs if extra_kwargs is not None else {}

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        del host_session, workspace
        module: ModuleType = import_module(self._module_name)
        handler = cast("ToolHandler", getattr(module, self._handler_name))
        return handler(self._session, self._workspace, params, **self._extra_kwargs)


class UpstreamProxyHandler:
    def __init__(self, alias: str, upstream_registry: UpstreamRegistry) -> None:
        self._alias = alias
        self._upstream_registry = upstream_registry

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        del host_session, workspace
        return self._upstream_registry.call_tool(self._alias, params)


class ToolBridge:
    """Registry for MCP tools and dispatcher for tool invocations."""

    def __init__(self, session: object | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._session = session

    def register(self, metadata: ToolMetadata, handler: RegistrationHandler) -> None:
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
        return [
            tool.metadata for tool in self._tools.values() if self._is_tool_allowed(tool.metadata)
        ]

    def list_definitions(self) -> list[ToolDefinition]:
        """Return public tool definitions in registration order."""
        return [
            tool.metadata.definition
            for tool in self._tools.values()
            if self._is_tool_allowed(tool.metadata)
        ]

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
        session = host_session or self._session
        if not self._is_tool_allowed(tool.metadata, session=session):
            capability = tool.metadata.required_capability
            raise ToolDispatchError(f"Tool '{name}' requires capability '{capability}'")
        tool_params = dict(params or {})
        try:
            return tool.handler(host_session, workspace, tool_params)
        except ToolBridgeError:
            raise
        except Exception as exc:
            raise ToolDispatchError(f"Tool '{name}' failed: {exc}") from exc

    def _is_tool_allowed(self, metadata: ToolMetadata, session: object | None = None) -> bool:
        effective_session = session or self._session
        if effective_session is None:
            return True

        checker = cast(
            "Callable[[str], object] | None", getattr(effective_session, "check_capability", None)
        )
        if not callable(checker):
            return True

        return _is_approved(checker(metadata.required_capability))


def _is_approved(outcome: object) -> bool:
    if outcome is True:
        return True
    if isinstance(outcome, str):
        return outcome.strip().lower() in {"approved", "allow", "allowed"}
    if isinstance(outcome, dict):
        mapping = cast("Mapping[str, object]", outcome)
        return any(
            isinstance(mapping.get(field), str)
            and cast("str", mapping[field]).strip().lower() in {"approved", "allow", "allowed"}
            for field in ("name", "value", "status")
        )
    for field in ("name", "value", "status"):
        value = cast("object", getattr(outcome, field, None))
        if isinstance(value, str) and value.strip().lower() in {"approved", "allow", "allowed"}:
            return True
    return False


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


def _tool_specs(mcp_config: McpConfig) -> tuple[ToolSpec, ...]:
    _specs: list[ToolSpec] = [
        ToolSpec(
            metadata=_metadata(
                name=READ_FILE_TOOL,
                description=(
                    "Read a UTF-8 text file from the workspace. "
                    "Required: path (string). Returns file contents as text. "
                    'Example: {"path": "README.md"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path, relative or absolute inside workspace "
                                "(example: 'README.md', '/tmp/file.txt')."
                            ),
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_read_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name=WRITE_FILE_TOOL,
                description=(
                    "Write UTF-8 text to a file, creating parent dirs as needed. "
                    "Required: path and content (both strings). Overwrites existing files. "
                    'Example: {"path": "tmp/notes.md", "content": "hello"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Destination path, relative or absolute inside workspace "
                                "(example: 'tmp/notes.md')."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "Text content to write to the file.",
                        },
                    },
                    "required": ["path", "content"],
                },
                required_capability="WorkspaceWriteAny",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_write_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name=LIST_DIRECTORY_TOOL,
                description=(
                    "List directory entries. Required: path (string). "
                    "Optional: recursive (boolean, default false). "
                    "Returns array of entries with type (file/dir) and path. "
                    'Example: {"path": "ralph", "recursive": false}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path, relative or absolute inside workspace "
                                "(example: '.', 'ralph')."
                            ),
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": (
                                "List subdirectories recursively. Default false."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_list_directory",
        ),
        ToolSpec(
            metadata=_metadata(
                name="search_files",
                description=(
                    "Search for files matching a glob pattern within a directory. "
                    "Required: pattern and path (both strings). "
                    'Example: {"pattern": "*.py", "path": "."}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Glob pattern to match (example: '*.py', '**/*.md')."
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory to search in, relative or absolute inside workspace "
                                "(example: 'ralph')."
                            ),
                        },
                    },
                    "required": ["pattern", "path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_search_files",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_STATUS_TOOL,
                description=(
                    "Get git status showing modified, staged, and untracked files. "
                    "No parameters required."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_status",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_DIFF_TOOL,
                description=(
                    "Get git diff showing line-by-line differences in modified files. "
                    "Optional: args (array of extra git diff arguments)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Extra git diff args (example: ['--stat'] for summary)."
                            ),
                        },
                    },
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_diff",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_LOG_TOOL,
                description=(
                    "Get git commit log with hash, author, date, and message. "
                    "Optional: count (number, default 10)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "number",
                            "description": "Number of recent commits to show (default: 10).",
                            "default": 10,
                        },
                    },
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_log",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_SHOW_TOOL,
                description=(
                    "Show a git object (commit, tag, tree, blob) with full details. "
                    "Required: ref (string, e.g. commit sha, branch, tag). "
                    'Example: {"ref": "HEAD~1"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": (
                                "Git reference: commit sha, branch name, or tag "
                                "(example: 'HEAD~1', 'main', 'v1.0')."
                            ),
                        },
                    },
                    "required": ["ref"],
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_show",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EXEC_TOOL,
                description=(
                    "Execute a shell command. Required: command (string, no shell operators). "
                    "Optional: args (string array) and timeout_ms (number, default 30000). "
                    "Returns stdout, stderr, exit_code. "
                    'Example: {"command": "ls", "args": ["-la"], "timeout_ms": 5000}. '
                    "Prefer structured tools when available."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "Command to execute without shell operators "
                                "(example: 'ls', 'git status')."
                            ),
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Command arguments as separate strings "
                                "(example: ['-la'])."
                            ),
                        },
                        "timeout_ms": {
                            "type": "number",
                            "description": (
                                "Timeout in milliseconds (default: 30000, example: 5000)."
                            ),
                            "default": 30000,
                        },
                    },
                    "required": ["command"],
                },
                required_capability="ProcessExecBounded",
            ),
            module_name="ralph.mcp.tools.exec",
            handler_name="handle_exec_command",
        ),
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_ARTIFACT_TOOL,
                description=(
                    "Submit a structured artifact (plan, development_result, issues, etc.). "
                    "Required: artifact_type (string). "
                    "Optional: content (JSON string) or content_path (path to JSON file). "
                    'Example: {"artifact_type": "plan", "content": "{\"summary\":{...}}"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "description": (
                                "Artifact type: plan, development_result, issues, "
                                "fix_result, commit_message, "
                                "development_analysis_decision, review_analysis_decision."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "JSON-serialized artifact payload as a string."
                            ),
                        },
                        "content_path": {
                            "type": "string",
                            "description": (
                                "Path to JSON file with artifact payload. "
                                "Use instead of content to submit from disk."
                            ),
                        },
                    },
                    "required": ["artifact_type"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_submit_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_PLAN_SECTION_TOOL,
                description=(
                    "Submit a single section of the plan artifact for incremental validation. "
                    "Required: section (string) and content (string). "
                    "Optional: mode (replace or append, default replace). "
                    "Call ralph_finalize_plan when all sections are staged."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": (
                                "Section name: summary, skills_mcp, steps, "
                                "critical_files, risks_mitigations, "
                                "verification_strategy, or parallel_plan."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "JSON-serialized section payload.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": (
                                "replace (default) overwrites; append adds to list."
                            ),
                        },
                    },
                    "required": ["section", "content"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_submit_plan_section",
        ),
        ToolSpec(
            metadata=_metadata(
                name=FINALIZE_PLAN_TOOL,
                description=(
                    "Validate the staged plan draft and write .agent/artifacts/plan.json. "
                    "Fails if required sections are missing; draft is preserved on failure."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_finalize_plan",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GET_PLAN_DRAFT_TOOL,
                description=(
                    "Return the currently staged plan draft (sections and contents). "
                    "Useful for resuming after restart or confirming state."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_get_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DISCARD_PLAN_DRAFT_TOOL,
                description="Delete the staged plan draft to start fresh.",
                input_schema={"type": "object", "properties": {}},
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.artifact",
            handler_name="handle_discard_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REPORT_PROGRESS_TOOL,
                description=(
                    "Report progress status to the agent orchestrator. "
                    "Required: status (string). Optional: note (string). "
                    'Example: {"status": "Processing 50/100 files"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "Status message describing current progress.",
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional additional context.",
                        },
                    },
                    "required": ["status"],
                },
                required_capability="RunReportProgress",
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_report_progress",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DECLARE_COMPLETE_TOOL,
                description=(
                    "Declare that the agent has completed its task. "
                    "Optional: summary (string describing what was accomplished). "
                    'Example: {"summary": "Fixed login bug"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Summary of what was accomplished.",
                        },
                    },
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_declare_complete",
        ),
        ToolSpec(
            metadata=_metadata(
                name=READ_ENV_TOOL,
                description=(
                    "Read an environment variable from the Ralph process. "
                    "Required: name (string). "
                    'Example: {"name": "HOME"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Environment variable name "
                                "(example: 'HOME', 'PATH')."
                            ),
                        },
                    },
                    "required": ["name"],
                },
                required_capability="EnvRead",
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_read_env",
        ),
        ToolSpec(
            metadata=_metadata(
                name=LIST_DIRECTORY_RECURSIVE_TOOL,
                description=(
                    "List all files and directories recursively starting from path. "
                    "Required: path (string). "
                    'Example: {"path": "ralph"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Root directory for recursive listing, relative or absolute "
                                "inside workspace (example: 'ralph')."
                            ),
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_list_directory_recursive",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DIRECTORY_TREE_TOOL,
                description=(
                    "Return a recursive directory tree showing all files and folders. "
                    "Compatibility alias for tools expecting standard directory_tree name. "
                    "Required: path (string). "
                    'Example: {"path": "."}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Root directory for tree view, relative or absolute "
                                "inside workspace (example: '.')."
                            ),
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_list_directory_recursive",
        ),
        ToolSpec(
            metadata=_metadata(
                name=COORDINATE_TOOL,
                description=(
                    "Coordinate parallel worker activities. "
                    "Required: action (claim, release, status, ack). "
                    "Optional: work_unit_id and payload. "
                    'Example: {"action": "claim", "work_unit_id": "task-001"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Coordination action: claim, release, status, or ack."
                            ),
                        },
                        "work_unit_id": {
                            "type": "string",
                            "description": "Optional work unit identifier.",
                        },
                        "payload": {
                            "type": "object",
                            "description": "Optional coordination payload as key-value pairs.",
                        },
                    },
                    "required": ["action"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tools.coordination",
            handler_name="handle_coordinate",
        ),
    ]
    if mcp_config.web_search.enabled:
        _specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=WEB_SEARCH_TOOL,
                    description=(
                        "Search the web using a multi-backend fallback chain. "
                        "Required: query (string). Optional: limit (integer, default 10, max 25). "
                        'Example: {"query": "python 3.12 features", "limit": 5}.'
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query string (example: 'python features').",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 25,
                                "description": "Max results to return (default: 10, max: 25).",
                                "default": 10,
                            },
                        },
                        "required": ["query"],
                    },
                    required_capability="WebSearch",
                ),
                module_name="ralph.mcp.tools.websearch",
                handler_name="handle_web_search",
            ),
        )
    return tuple(_specs)


def _attach_upstream_registry(bridge: ToolBridge, upstream_registry: UpstreamRegistry) -> None:
    for proxied_tool in upstream_registry.tool_definitions():
        metadata = ToolMetadata(
            definition=ToolDefinition(
                name=proxied_tool.alias,
                description=proxied_tool.tool.description,
                input_schema=proxied_tool.tool.input_schema,
            ),
            required_capability=McpCapability.UPSTREAM_TOOL_USE,
        )
        handler = UpstreamProxyHandler(
            alias=proxied_tool.alias,
            upstream_registry=upstream_registry,
        )
        bridge.register(metadata, handler)


def build_ralph_tool_registry(
    session: object,
    workspace: object,
    *,
    upstream_registry: UpstreamRegistry | None = None,
    mcp_config: McpConfig | None = None,
) -> ToolBridge:
    """Build the default Ralph MCP tool registry.

    This mirrors the Rust `build_ralph_tool_registry` function. The returned
    registry captures `session` and `workspace` so future tool module ports can
    expose Rust-compatible `handle_*` functions without extra adapter glue.
    """

    from ralph.config.mcp_models import McpConfig  # noqa: PLC0415

    mcp_cfg = mcp_config or McpConfig()
    bridge = ToolBridge(session=session)
    for spec in _tool_specs(mcp_cfg):
        is_websearch = (
            spec.module_name == "ralph.mcp.tools.websearch"
            and spec.handler_name == "handle_web_search"
        )
        if is_websearch:
            bridge.register(
                spec.metadata,
                LazyToolHandler(
                    module_name=spec.module_name,
                    handler_name=spec.handler_name,
                    session=session,
                    workspace=workspace,
                    extra_kwargs={"web_search_config": mcp_cfg.web_search},
                ),
            )
        else:
            bridge.register_spec(spec, session=session, workspace=workspace)
    if upstream_registry is not None:
        _attach_upstream_registry(bridge, upstream_registry)
    return bridge


__all__ = [
    "LazyToolHandler",
    "RegisteredTool",
    "RegistrationHandler",
    "ToolBridge",
    "ToolBridgeError",
    "ToolDefinition",
    "ToolDispatchError",
    "ToolMetadata",
    "ToolRegistrationError",
    "ToolSpec",
    "UpstreamProxyHandler",
    "build_ralph_tool_registry",
]
