"""Tool specs for file write operations."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    APPEND_FILE_TOOL,
    COPY_FILE_TOOL,
    CREATE_DIRECTORY_TOOL,
    DELETE_PATH_TOOL,
    EDIT_FILE_TOOL,
    MOVE_FILE_TOOL,
)


def file_write_specs() -> list[ToolSpec]:
    """Return tool specs for file write operations."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=EDIT_FILE_TOOL,
                description=(
                    "Structured file edit with precise text replacement, optional dry-run, "
                    "and optional indexed target/hashing/impact-preview arguments. "
                    "Required params: path (string), edits (list of {oldText, newText}). "
                    "Optional: dry_run (bool), expected_content_hash (SHA-256 string "
                    "fail-closed when current file hash mismatches), target "
                    "({evidence_id|span_id|symbol}) to anchor an edit, match_strategy "
                    "('exact'|'within_target'|'all_in_target', default 'exact'), reindex "
                    "('auto'|'skip'|'changed_blocking', default 'auto'), impact_preview "
                    "(bool, default False), return_evidence_updates (bool, default False). "
                    "Each edit replaces the first match of oldText unless target + "
                    "match_strategy change the anchoring. "
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
                        "expected_content_hash": {
                            "type": "string",
                            "description": (
                                "Precondition: fail closed when the file's current "
                                "SHA-256 does not match this value."
                            ),
                        },
                        "target": {
                            "type": "object",
                            "description": (
                                "Indexed anchor for the edit. One of "
                                '{"evidence_id": "..."}, {"span_id": "..."}, '
                                'or {"symbol": "...", "path": "..."}. When '
                                "omitted the legacy oldText/newText anchor is used."
                            ),
                            "additionalProperties": True,
                        },
                        "match_strategy": {
                            "type": "string",
                            "enum": ["exact", "within_target", "all_in_target"],
                            "description": (
                                "Anchoring strategy when ``target`` is set. "
                                "``exact`` requires the edit's oldText to be "
                                "the indexed span; ``within_target`` accepts "
                                "an occurrence inside the span; ``all_in_target`` "
                                "rejects edits that cross span boundaries."
                            ),
                            "default": "exact",
                        },
                        "reindex": {
                            "type": "string",
                            "enum": ["auto", "skip", "changed_blocking"],
                            "description": (
                                "Reindex policy. ``auto`` marks the path "
                                "dirty and lets the lifecycle refresh; ``skip`` "
                                "marks dirty without triggering a refresh; "
                                "``changed_blocking`` runs a bounded refresh "
                                "after a successful edit before returning."
                            ),
                            "default": "auto",
                        },
                        "impact_preview": {
                            "type": "boolean",
                            "description": (
                                "Include conservative graph-based impact (callers, "
                                "importers, tests) for the anchored symbol when "
                                "dry_run=True and the explore index is available."
                            ),
                            "default": False,
                        },
                        "return_evidence_updates": {
                            "type": "boolean",
                            "description": (
                                "Return updated generation + freshness metadata "
                                "after a successful edit."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["path", "edits"],
                },
                required_capability=McpCapability.WORKSPACE_EDIT.value,
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
                required_capability=McpCapability.WORKSPACE_EDIT.value,
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
                required_capability=McpCapability.WORKSPACE_EDIT.value,
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
                required_capability=McpCapability.WORKSPACE_EDIT.value,
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
                required_capability=McpCapability.WORKSPACE_EDIT.value,
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
                required_capability=McpCapability.WORKSPACE_DELETE.value,
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_delete_path",
        ),
    ]
