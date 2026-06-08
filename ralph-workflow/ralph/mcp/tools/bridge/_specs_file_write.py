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
