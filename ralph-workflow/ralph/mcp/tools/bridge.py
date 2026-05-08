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
    APPEND_FILE_TOOL,
    COORDINATE_TOOL,
    COPY_FILE_TOOL,
    CREATE_DIRECTORY_TOOL,
    DECLARE_COMPLETE_TOOL,
    DELETE_PATH_TOOL,
    DIRECTORY_TREE_TOOL,
    DISCARD_PLAN_DRAFT_TOOL,
    EDIT_FILE_TOOL,
    EXEC_TOOL,
    FINALIZE_PLAN_TOOL,
    GET_PLAN_DRAFT_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    GREP_FILES_TOOL,
    LIST_ALLOWED_ROOTS_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    LIST_DIRECTORY_TOOL,
    MOVE_FILE_TOOL,
    READ_ENV_TOOL,
    READ_FILE_TOOL,
    READ_IMAGE_TOOL,
    READ_MULTIPLE_FILES_TOOL,
    REPORT_PROGRESS_TOOL,
    SEARCH_FILES_TOOL,
    STAT_PATH_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_PLAN_SECTION_TOOL,
    VISIT_URL_TOOL,
    WEB_SEARCH_TOOL,
    WRITE_FILE_TOOL,
)

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.upstream.registry import UpstreamRegistry

JsonObject = dict[str, object]
ToolHandler = Callable[[object, object, JsonObject], object]

_EXAMPLE_PLAN_CONTENT = '{"summary": "placeholder"}'
_EXAMPLE_COMMIT_CONTENT = '{"type": "commit", "subject": "placeholder"}'
_EXAMPLE_STEPS_CONTENT = '{"steps": [{"step": "placeholder"}]}'


class RegistrationHandler(Protocol):
    """Callable protocol for MCP tool handler functions registered in the tool bridge."""

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
    is_multimodal: bool = False


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
    """Proxy handler that forwards tool calls to an upstream MCP registry."""

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
        self._client_capabilities: set[str] | None = None

    def set_client_capabilities(self, capabilities: set[str] | None) -> None:
        """Set the client declared capabilities from MCP initialize handshake."""
        self._client_capabilities = capabilities

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
            tool.metadata
            for tool in self._tools.values()
            if self._is_tool_allowed(tool.metadata) and self._is_tool_visible(tool.metadata)
        ]

    def list_definitions(self) -> list[ToolDefinition]:
        """Return public tool definitions in registration order."""
        return [
            tool.metadata.definition
            for tool in self._tools.values()
            if self._is_tool_allowed(tool.metadata) and self._is_tool_visible(tool.metadata)
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

    def _is_tool_visible(self, metadata: ToolMetadata) -> bool:
        """Check if a tool is visible to the client based on multimodal flags."""
        if not metadata.is_multimodal:
            return True

        if self._client_capabilities is None:
            return False

        client_caps = self._client_capabilities
        return (
            "image" in client_caps
            or "media" in client_caps
            or "multimodal" in client_caps
            or "MediaRead" in client_caps
            or "media.read" in client_caps
        )

    def _is_tool_allowed(self, metadata: ToolMetadata, session: object | None = None) -> bool:
        effective_session = session or self._session
        if effective_session is None:
            return True

        checker = cast(
            "Callable[[str], object] | None",
            getattr(effective_session, "check_capability", None),
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
    is_multimodal: bool = False,
) -> ToolMetadata:
    return ToolMetadata(
        definition=ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
        ),
        required_capability=required_capability,
        is_multimodal=is_multimodal,
    )


_SUBMIT_ARTIFACT_DESCRIPTION = (
    "Submit a structured artifact (plan, development_result, issues, etc.). "
    "Required params: artifact_type (string) and content (JSON string). "
    "Returns confirmation on success. "
    'Example: {"artifact_type": "plan", "content": "{\\"summary\\": {}}"} '
    "submits a plan artifact. On error, read the format doc at "
    ".agent/artifact-formats/<type>.md (or .agent/artifact-formats/artifact_formats_index.md "
    "if artifact_type is unknown) before retrying."
)


def _tool_specs(mcp_config: McpConfig) -> tuple[ToolSpec, ...]:
    _specs: list[ToolSpec] = [
        ToolSpec(
            metadata=_metadata(
                name=READ_FILE_TOOL,
                description=(
                    "Read the complete contents of a file as text. "
                    "Required param: path (string, relative or absolute path inside workspace). "
                    "Optional params for partial reads: line_start (1-based), line_end (1-based), "
                    "offset (0-based byte offset), limit (byte limit), head (first N lines), "
                    "tail (last N lines). "
                    "When partial params are used, returns JSON with path, content, total_lines, "
                    "returned_lines, truncated. Otherwise returns plain text. "
                    'Example: {"path": "ralph-workflow/README.md"} returns the file text.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path as a string, relative or absolute inside the workspace "
                                "(example values: 'README.md', '/tmp/file.txt', "
                                "'ralph-workflow/ralph/__init__.py')."
                            ),
                        },
                        "line_start": {
                            "type": "integer",
                            "description": (
                                "1-based line number to start from (inclusive). "
                                "Use with line_end for a line range. "
                                "MUTUALLY EXCLUSIVE with offset/limit and head/tail."
                            ),
                        },
                        "line_end": {
                            "type": "integer",
                            "description": (
                                "1-based line number to end at (inclusive). "
                                "MUTUALLY EXCLUSIVE with offset/limit and head/tail."
                            ),
                        },
                        "offset": {
                            "type": "integer",
                            "description": (
                                "0-based byte offset to start reading from. "
                                "MUTUALLY EXCLUSIVE with line_start/line_end and head/tail."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Maximum number of bytes to read from offset. "
                                "MUTUALLY EXCLUSIVE with line_start/line_end and head/tail."
                            ),
                        },
                        "head": {
                            "type": "integer",
                            "description": (
                                "Return only the first N lines. "
                                "MUTUALLY EXCLUSIVE with line_start/line_end and offset/limit."
                            ),
                        },
                        "tail": {
                            "type": "integer",
                            "description": (
                                "Return only the last N lines. "
                                "MUTUALLY EXCLUSIVE with line_start/line_end and offset/limit."
                            ),
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": (
                                "Maximum bytes for a full-file read before truncating "
                                "(default: 5_000_000). Ignored when partial-read params are used."
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
                    "Write UTF-8 text to a file in the workspace, creating parent directories as "
                    "needed. Required params: path (string) and content (string). Overwrites "
                    "existing files if they exist. Returns nothing on success. "
                    'Example: {"path": "tmp/notes.md", "content": "hello world"} creates '
                    "or overwrites tmp/notes.md with 'hello world'."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Destination file path as a string, relative or absolute inside "
                                "the workspace (example values: 'tmp/notes.md', "
                                "'docs/changes.txt', '/tmp/output.json')."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Text content to write as a UTF-8 string "
                                "(example values: 'hello world', "
                                "'# Heading\\n\\nSome content here', "
                                '\'{"key": "value"}\').'
                            ),
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
                name=READ_MULTIPLE_FILES_TOOL,
                description=(
                    "Read multiple text files in one call. "
                    "Required param: paths (list of strings). "
                    "Returns per-file success/failure rather than failing the entire batch. "
                    'Example: {"paths": ["file1.txt", "file2.txt"]} returns JSON with files array.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of file paths to read.",
                        },
                    },
                    "required": ["paths"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_read_multiple_files",
        ),
        ToolSpec(
            metadata=_metadata(
                name=STAT_PATH_TOOL,
                description=(
                    "Get file info/stat data. Required param: path (string). "
                    "Returns file type ('file'|'dir'|'missing'), size_bytes, created_unix, "
                    "modified_unix, and mode. "
                    'Example: {"path": "README.md"} returns file metadata as JSON.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to get metadata for.",
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceMetadataRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_stat",
        ),
        ToolSpec(
            metadata=_metadata(
                name=LIST_ALLOWED_ROOTS_TOOL,
                description=(
                    "Expose the list of allowed directories/roots for the workspace. "
                    "No parameters required. Returns JSON list of allowed root paths. "
                    "Example: {} returns the configured allowed roots."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_list_allowed_roots",
        ),
        ToolSpec(
            metadata=_metadata(
                name=LIST_DIRECTORY_TOOL,
                description=(
                    "List entries in a directory. Required param: path (string). "
                    "Optional param: recursive (boolean, default false). "
                    "Returns an array of entries, each with type ('file' or 'dir'), "
                    "name, and relative path. "
                    'Example: {"path": "ralph-workflow/ralph", "recursive": false} '
                    "lists files and folders in the ralph directory without recursion."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path as a string, relative or absolute inside the "
                                "workspace (example values: '.', 'ralph', "
                                "'ralph-workflow/ralph/mcp')."
                            ),
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": (
                                "Whether to list subdirectories recursively as a boolean "
                                "(example values: false, true)."
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
                name=SEARCH_FILES_TOOL,
                description=(
                    "Search for files matching a true glob pattern within a directory. "
                    "Required params: pattern (string, glob style) and path (string). "
                    "Optional params: exclude (list of glob patterns), limit (default 1000). "
                    "Returns JSON with pattern, base, matches array, and truncated flag. "
                    "Supports ** for any-depth, * for single segment, ? for single char. "
                    'Example: {"pattern": "**/*.py", "path": "."} matches Python files recursively.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Glob pattern as a string to match file names "
                                "(example values: '*.py', '**/*.md', 'test_*.py', "
                                "'config/*.toml', '**/test_*.py')."
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path as a string to search inside, relative or "
                                "absolute inside the workspace "
                                "(example values: '.', 'ralph', 'tests')."
                            ),
                        },
                        "exclude": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of glob patterns to exclude from results.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default 1000).",
                            "default": 1000,
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
                name=GREP_FILES_TOOL,
                description=(
                    "Native content search (grep/regex). Required: pattern (string), "
                    "path (string, base directory). Optional: regex (bool, default True), "
                    "case_sensitive (bool), whole_word (bool), include/exclude (glob patterns), "
                    "context_before/after (int), limit (int, default 1000), "
                    "max_file_bytes (int). "
                    "Returns JSON: matches (path, line, text, context_before/after), "
                    "truncated, skipped_files. "
                    'Example: {"pattern": "def main", "path": ".", "case_sensitive": false}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Search pattern (regex if regex=True, else literal)."
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": "Base directory to search under.",
                        },
                        "regex": {
                            "type": "boolean",
                            "description": "Treat pattern as regex (default True).",
                            "default": True,
                        },
                        "case_sensitive": {
                            "type": "boolean",
                            "description": "Case sensitive search (default True).",
                            "default": True,
                        },
                        "whole_word": {
                            "type": "boolean",
                            "description": "Match whole words only (default False).",
                            "default": False,
                        },
                        "include": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Glob patterns to include files from.",
                        },
                        "exclude": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Glob patterns to exclude files from.",
                        },
                        "context_before": {
                            "type": "integer",
                            "description": "Number of context lines before each match (default 0).",
                            "default": 0,
                        },
                        "context_after": {
                            "type": "integer",
                            "description": "Number of context lines after each match (default 0).",
                            "default": 0,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum matches to return (default 1000).",
                            "default": 1000,
                        },
                        "max_file_bytes": {
                            "type": "integer",
                            "description": "Skip files larger than this (default 5_000_000).",
                            "default": 5000000,
                        },
                    },
                    "required": ["pattern", "path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_grep_files",
        ),
        ToolSpec(
            metadata=_metadata(
                name=LIST_DIRECTORY_RECURSIVE_TOOL,
                description=(
                    "List all files and directories recursively starting from path. "
                    "Required param: path (string). Returns a flat array of all entries "
                    "with their full paths. "
                    'Example: {"path": "ralph"} returns all files and folders under ralph/.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Root directory path as a string for recursive listing, "
                                "relative or absolute inside the workspace "
                                "(example values: 'ralph', '.', 'src')."
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
                    "Return a structured JSON directory tree. Required param: path (string). "
                    "Optional params: max_depth (integer, unlimited if None), "
                    "exclude_patterns (list of glob patterns to exclude). "
                    "Returns a nested dict with name, type ('dir'|'file'), and children key "
                    "for directories only. "
                    'Example: {"path": ".", "max_depth": 2} returns a 2-level JSON tree.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Root directory path as a string for tree view, relative or "
                                "absolute inside the workspace "
                                "(example values: '.', 'ralph', 'src')."
                            ),
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum depth to recurse to (None = unlimited).",
                        },
                        "exclude_patterns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Glob patterns to exclude from the tree.",
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceRead",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_directory_tree",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EDIT_FILE_TOOL,
                description=(
                    "Structured file edit with precise text replacement and optional dry-run. "
                    "Required params: path (string), edits (list of {oldText, newText} objects). "
                    "Each edit replaces first match of oldText. "
                    "Optional: dry_run (bool, default False). "
                    "When dry_run=True, returns diff preview without writing. "
                    "When dry_run=False, applies edits and returns diff and bytes_written. "
                    "Returns error with status='no_match' when oldText is not found. "
                    'Example: {"path": "f.txt", "edits": [{"oldText": "foo", "newText": "bar"}]}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to edit.",
                        },
                        "edits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "oldText": {"type": "string"},
                                    "newText": {"type": "string"},
                                },
                                "required": ["oldText"],
                            },
                            "description": "List of edits to apply in order.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview changes without writing (default False).",
                            "default": False,
                        },
                    },
                    "required": ["path", "edits"],
                },
                required_capability="WorkspaceEdit",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_edit_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name=APPEND_FILE_TOOL,
                description=(
                    "Append content to a file. Required params: path (string), content (string). "
                    "Creates the file if it doesn't exist. "
                    'Example: {"path": "log.txt", "content": "new line\\n"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to append to.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to append.",
                        },
                    },
                    "required": ["path", "content"],
                },
                required_capability="WorkspaceEdit",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_append_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name=CREATE_DIRECTORY_TOOL,
                description=(
                    "Create a directory and all parent directories. Required param: path (string). "
                    'Example: {"path": "new/nested/dir"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path to create.",
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceEdit",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_create_directory",
        ),
        ToolSpec(
            metadata=_metadata(
                name=MOVE_FILE_TOOL,
                description=(
                    "Move or rename a file or directory. Required params: src (string), "
                    "dest (string). Optional: overwrite (bool, default False). "
                    "Fails if dest exists and overwrite is False. "
                    'Example: {"src": "old.txt", "dest": "new.txt", "overwrite": false}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "src": {
                            "type": "string",
                            "description": "Source file path.",
                        },
                        "dest": {
                            "type": "string",
                            "description": "Destination file path.",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Overwrite existing destination (default False).",
                            "default": False,
                        },
                    },
                    "required": ["src", "dest"],
                },
                required_capability="WorkspaceEdit",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_move_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name=COPY_FILE_TOOL,
                description=(
                    "Copy a file or directory. Required params: src (string), dest (string). "
                    "Optional: overwrite (bool, default False). "
                    "Fails if dest exists and overwrite is False. "
                    'Example: {"src": "original.txt", "dest": "copy.txt", "overwrite": false}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "src": {
                            "type": "string",
                            "description": "Source file path.",
                        },
                        "dest": {
                            "type": "string",
                            "description": "Destination file path.",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Overwrite existing destination (default False).",
                            "default": False,
                        },
                    },
                    "required": ["src", "dest"],
                },
                required_capability="WorkspaceEdit",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_copy_file",
        ),
        ToolSpec(
            metadata=_metadata(
                name=DELETE_PATH_TOOL,
                description=(
                    "Delete a file or directory. Required param: path (string). "
                    "Optional: recursive (bool, default False). "
                    "DISTINCT: requires WorkspaceDelete capability (not WorkspaceEdit). "
                    "Refuses to delete directories unless recursive=True. "
                    'Example: {"path": "old_file.txt", "recursive": false}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to delete.",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Delete directories recursively (default False).",
                            "default": False,
                        },
                    },
                    "required": ["path"],
                },
                required_capability="WorkspaceDelete",
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_delete_path",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_STATUS_TOOL,
                description=(
                    "Get git status showing modified, staged, and untracked files. "
                    "No parameters required. Returns a status object with lists of modified, "
                    "staged, and untracked files. "
                    "Example: {} returns git status output."
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
                    "Optional param: args (array of extra git diff arguments as strings). "
                    "Returns diff output with line changes. "
                    'Example: {"args": []} shows full diff; '
                    '{"args": ["--stat"]} shows summary only.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Array of extra git diff arguments as strings "
                                "(example values: [], ['--stat'], ['--name-only'])."
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
                    "Optional param: count (number, default 10). "
                    "Returns an array of commit objects. "
                    'Example: {"count": 5} returns the 5 most recent commits.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "number",
                            "description": (
                                "Number of recent commits to return as an integer "
                                "(default: 10, example values: 5, 20, 100)."
                            ),
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
                    "Required param: ref (string, git object reference). "
                    "Returns the object contents. "
                    'Example: {"ref": "HEAD~1"} shows the parent commit; '
                    '{"ref": "v1.0.0"} shows the tag details.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": (
                                "Git object reference as a string such as a commit SHA, branch "
                                "name, or tag "
                                "(example values: 'HEAD~1', 'main', 'v1.0.0', 'abc123def')."
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
                    "Execute a bounded subprocess in the workspace. Accepts command or "
                    "argv as a string or string array, plus optional args and timeout_ms. "
                    "Shell-style strings are tokenized, but shell control operators are "
                    "rejected because exec does not run a shell. Returns stdout, stderr, "
                    "and exit_code. Example: {\"command\": \"python -m pytest\", "
                    '\"args\": [\"-q\"], \"timeout_ms\": 5000}. '
                    "Some commands may still be blacklisted; prefer structured tools "
                    "when available."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": (
                                "Primary command input. This may be a bare executable "
                                "name, a shell-style command line without shell control "
                                "operators, or an argv-style string array (example values: "
                                "'ls', 'python --version', 'python -m pytest "
                                "tests/test_tool_exec.py', ['python', '-m', 'pytest'])."
                            ),
                        },
                        "argv": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": (
                                "Fallback alias for callers that prefer argv-style input. Used "
                                "when 'command' is omitted. Accepts the same forms as 'command'."
                            ),
                        },
                        "args": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "string"},
                            ],
                            "description": (
                                "Optional command arguments. Pass either an array of strings "
                                "or a shell-style string without shell control operators "
                                "(example values: ['-la'], '--help', ['.', '--max-depth', '2'])."
                            ),
                        },
                        "timeout_ms": {
                            "type": "number",
                            "description": (
                                "Timeout in milliseconds as a number "
                                "(default: 30000, example values: 5000, 10000, 60000)."
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
                description=_SUBMIT_ARTIFACT_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "description": (
                                "Type of artifact as a string: plan, development_result, "
                                "issues, fix_result, commit_message, "
                                "development_analysis_decision, planning_analysis_decision, "
                                "or review_analysis_decision "
                                "(example values: 'plan', 'development_result', 'issues')."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Artifact payload as a JSON-serialized string "
                                "(example values: "
                                + _EXAMPLE_PLAN_CONTENT
                                + ", "
                                + _EXAMPLE_COMMIT_CONTENT
                                + ")."
                            ),
                        },
                    },
                    "required": ["artifact_type", "content"],
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
                    "Required params: section (string) and content (string). Optional param: "
                    "mode (string, 'replace' or 'append', default 'replace'). "
                    "Call ralph_finalize_plan when all sections are staged. "
                    'Example: {"section": "summary", "content": '
                    + _EXAMPLE_PLAN_CONTENT
                    + ', "mode": "replace"} updates the summary section.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": (
                                "Section name as a string: summary, skills_mcp, steps, "
                                "critical_files, risks_mitigations, verification_strategy, "
                                "or parallel_plan "
                                "(example values: 'summary', 'steps', 'risks_mitigations')."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "JSON-serialized section payload as a string "
                                "(example values: "
                                + _EXAMPLE_PLAN_CONTENT
                                + ", "
                                + _EXAMPLE_STEPS_CONTENT
                                + ")."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": (
                                "Mode as a string: 'replace' overwrites the section "
                                "(default), 'append' adds to a list section "
                                "(example values: 'replace', 'append')."
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
                    "Fails with an error if required sections are missing; "
                    "the draft is preserved on failure. No parameters required. "
                    "Example: {} validates and writes the plan."
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
                    "Return the currently staged plan draft with all sections and contents. "
                    "Useful for resuming after a restart or confirming current state. "
                    "No parameters required. "
                    "Example: {} returns the current draft state."
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
                description=(
                    "Delete the staged plan draft to start fresh. "
                    "No parameters required. Use with caution as this cannot be undone. "
                    "Example: {} deletes the current draft."
                ),
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
                    "Required param: status (string). Optional param: note (string). "
                    "Returns confirmation on success. "
                    'Example: {"status": "Processing 50/100 files", "note": "Phase 2 of 3"} '
                    "reports current progress with an optional note."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": (
                                "Status message describing current progress as a string "
                                "(example values: 'Processing 50/100 files', "
                                "'Running tests...', 'Complete')."
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "Optional additional context or details as a string "
                                "(example values: 'Phase 2 of 3', 'Expected: 2 min')."
                            ),
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
                    "Optional param: summary (string describing what was accomplished). "
                    "Returns confirmation on success. "
                    'Example: {"summary": "Fixed login bug and added tests"} '
                    "signals task completion with a summary."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": (
                                "Summary of what was accomplished as a string "
                                "(example values: 'Fixed login bug', "
                                "'Completed refactor of auth module')."
                            ),
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
                    "Required param: name (string). "
                    "Returns the environment variable value as a string, or null if not set. "
                    'Example: {"name": "HOME"} returns the home directory path.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Environment variable name as a string "
                                "(example values: 'HOME', 'PATH', 'USER', 'EDITOR')."
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
                name=COORDINATE_TOOL,
                description=(
                    "Coordinate parallel worker activities. "
                    "Required param: action (string, one of: claim, release, status, ack). "
                    "Optional params: work_unit_id (string) and payload (object). "
                    "Returns coordination result. "
                    'Example: {"action": "claim", "work_unit_id": "task-001"} '
                    "claims the work unit 'task-001'."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Coordination action as a string: claim, release, status, or "
                                "ack (example values: 'claim', 'release', 'status', 'ack')."
                            ),
                        },
                        "work_unit_id": {
                            "type": "string",
                            "description": (
                                "Work unit identifier as a string "
                                "(example values: 'task-001', 'worker-5', 'build-123')."
                            ),
                        },
                        "payload": {
                            "type": "object",
                            "description": (
                                "Optional coordination payload as a key-value object "
                                "(example values: {'priority': 'high'}, {'status': 'ready'})."
                            ),
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
                        "Required param: query (string). Optional param: limit (integer, "
                        "default 10, max 25). Returns search results with titles, URLs, "
                        "and snippets. "
                        'Example: {"query": "python 3.12 features", "limit": 5} '
                        "returns 5 search results about Python 3.12."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Search query as a string "
                                    "(example values: 'python features', 'rust async')."
                                ),
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 25,
                                "description": (
                                    "Maximum number of results to return as an integer "
                                    "(default: 10, max: 25, example values: 5, 10, 20)."
                                ),
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
    if mcp_config.web_visit.enabled:
        _specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=VISIT_URL_TOOL,
                    description=(
                        "Fetch a single URL and return readable extracted text. "
                        "Required param: url (string, http/https). "
                        "Optional param: with_links (boolean, default false) to also include "
                        "up to 100 absolute outbound links. "
                        "Returns JSON with status, title, effective_url, content_type, text, "
                        "and optional links. "
                        "On failure returns is_error=true with a status code "
                        "(timeout, unreachable, http_error, unsupported_content, too_large, "
                        "blocked_by_policy, invalid_url)."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": (
                                    "URL to fetch as a string, must use http or https scheme "
                                    "(example values: 'https://example.com/', "
                                    "'https://docs.python.org/3/')."
                                ),
                            },
                            "with_links": {
                                "type": "boolean",
                                "description": (
                                    "Whether to include up to 100 absolute outbound links "
                                    "extracted from the page (default: false)."
                                ),
                                "default": False,
                            },
                        },
                        "required": ["url"],
                    },
                    required_capability="WebVisit",
                ),
                module_name="ralph.mcp.tools.webvisit",
                handler_name="handle_visit_url",
            ),
        )
    if mcp_config.media.enabled:
        _specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=READ_IMAGE_TOOL,
                    description=(
                        "Read an image file and return it as a base64-encoded content block. "
                        "Requires MediaRead capability and explicit media support enablement. "
                        "Required param: path (string, relative or absolute path). "
                        "Returns an image content block with type, base64 data, and MIME type. "
                        "Supported formats: png, jpg, jpeg, gif, webp. "
                        'Example: {"path": "docs/screenshot.png"} returns the image as base64.'
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "File path as a string, relative or absolute inside "
                                    "the workspace (example values: 'docs/screenshot.png')."
                                ),
                            },
                        },
                        "required": ["path"],
                    },
                    required_capability="media.read",
                    is_multimodal=True,
                ),
                module_name="ralph.mcp.tools.workspace",
                handler_name="handle_read_image",
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
    """Build the default Ralph MCP tool registry."""

    from ralph.config.mcp_models import McpConfig  # noqa: PLC0415

    mcp_cfg = mcp_config or McpConfig()
    bridge = ToolBridge(session=session)
    for spec in _tool_specs(mcp_cfg):
        is_websearch = (
            spec.module_name == "ralph.mcp.tools.websearch"
            and spec.handler_name == "handle_web_search"
        )
        is_webvisit = (
            spec.module_name == "ralph.mcp.tools.webvisit"
            and spec.handler_name == "handle_visit_url"
        )
        is_read_image = (
            spec.module_name == "ralph.mcp.tools.workspace"
            and spec.handler_name == "handle_read_image"
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
        elif is_webvisit:
            bridge.register(
                spec.metadata,
                LazyToolHandler(
                    module_name=spec.module_name,
                    handler_name=spec.handler_name,
                    session=session,
                    workspace=workspace,
                    extra_kwargs={"web_visit_config": mcp_cfg.web_visit},
                ),
            )
        elif is_read_image:
            bridge.register(
                spec.metadata,
                LazyToolHandler(
                    module_name=spec.module_name,
                    handler_name=spec.handler_name,
                    session=session,
                    workspace=workspace,
                    extra_kwargs={"max_inline_bytes": mcp_cfg.media.max_inline_bytes},
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
