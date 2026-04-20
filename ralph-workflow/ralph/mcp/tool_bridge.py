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

from ralph.mcp.capability_mapping import McpCapability
from ralph.mcp.tool_names import (
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
    from ralph.mcp.upstream_registry import UpstreamRegistry

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
                name=WRITE_FILE_TOOL,
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
                name=LIST_DIRECTORY_TOOL,
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
                name=GIT_STATUS_TOOL,
                description="Get git status of the workspace",
                input_schema={"type": "object", "properties": {}},
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tool_git_read",
            handler_name="handle_git_status",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_DIFF_TOOL,
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
                name=GIT_LOG_TOOL,
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
                name=GIT_SHOW_TOOL,
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
                name=EXEC_TOOL,
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
                name=SUBMIT_ARTIFACT_TOOL,
                description="Submit a structured artifact",
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "description": (
                                "Type of artifact (plan, development_result, issues, "
                                "fix_result, commit_message, development_analysis_decision, "
                                "review_analysis_decision; generic analysis_decision is only "
                                "valid inside an analysis drain)"
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "JSON-serialized artifact payload",
                        },
                        "content_path": {
                            "type": "string",
                            "description": (
                                "Path to a JSON file containing the raw artifact payload. "
                                "Use this to resubmit an edited artifact from disk instead "
                                "of inlining the JSON in the tool call."
                            ),
                        },
                    },
                    "required": ["artifact_type"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_artifact",
            handler_name="handle_submit_artifact",
        ),
        ToolSpec(
            metadata=_metadata(
                name=SUBMIT_PLAN_SECTION_TOOL,
                description=(
                    "Submit a single section of the plan artifact for incremental "
                    "validation. The schema matches the PlanArtifact documented in "
                    "the planning prompt — this tool just lets you build it piece "
                    "by piece on the server so errors can be fixed without "
                    "resending the whole plan. Each call validates the section and "
                    "merges it into a draft at .agent/artifacts/.plan_draft.json. "
                    "Call ralph_finalize_plan when all required sections are "
                    "staged, or fall back to ralph_submit_artifact with "
                    "artifact_type='plan' to submit atomically."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": (
                                "Plan section name: summary, skills_mcp, steps, "
                                "critical_files, risks_mitigations, "
                                "verification_strategy, or parallel_plan."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "JSON-serialized section payload. Shape matches "
                                "the corresponding field in PlanArtifact. For "
                                "list sections (steps, risks_mitigations, "
                                "verification_strategy, parallel_plan) pass the "
                                "full list in replace mode or a single item in "
                                "append mode."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": (
                                "replace (default) overwrites the section; "
                                "append adds a single item to a list section."
                            ),
                        },
                    },
                    "required": ["section", "content"],
                },
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_artifact",
            handler_name="handle_submit_plan_section",
        ),
        ToolSpec(
            metadata=_metadata(
                name=FINALIZE_PLAN_TOOL,
                description=(
                    "Validate the staged plan draft as a complete PlanArtifact "
                    "and write .agent/artifacts/plan.json. Fails with a "
                    "cross-section validation error if required sections are "
                    "missing or invariants are violated; the draft is preserved "
                    "on failure so you can fix and retry."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_artifact",
            handler_name="handle_finalize_plan",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GET_PLAN_DRAFT_TOOL,
                description=(
                    "Return the currently staged plan draft (which sections are "
                    "present and their contents). Useful for resuming after a "
                    "restart or confirming state before finalizing."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_artifact",
            handler_name="handle_get_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DISCARD_PLAN_DRAFT_TOOL,
                description=(
                    "Delete the staged plan draft so the next plan can start from scratch."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="ArtifactSubmit",
            ),
            module_name="ralph.mcp.tool_artifact",
            handler_name="handle_discard_plan_draft",
        ),
        ToolSpec(
            metadata=_metadata(
                name=REPORT_PROGRESS_TOOL,
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
                name=DECLARE_COMPLETE_TOOL,
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
                name=READ_ENV_TOOL,
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
                name=LIST_DIRECTORY_RECURSIVE_TOOL,
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
                name=DIRECTORY_TREE_TOOL,
                description=(
                    "Return a recursive directory tree. Compatibility alias for tools "
                    "that expect the standard filesystem MCP `directory_tree` name."
                ),
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
                name=COORDINATE_TOOL,
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
    ]
    if mcp_config.web_search.enabled:
        _specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=WEB_SEARCH_TOOL,
                    description="Search the web using a multi-backend fallback chain",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "search query"},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 25,
                                "default": 10,
                            },
                        },
                        "required": ["query"],
                    },
                    required_capability="WebSearch",
                ),
                module_name="ralph.mcp.tool_websearch",
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
            spec.module_name == "ralph.mcp.tool_websearch"
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
