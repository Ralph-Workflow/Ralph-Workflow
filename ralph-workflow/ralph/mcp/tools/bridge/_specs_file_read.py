"""Tool specs for file read operations."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    LIST_ALLOWED_ROOTS_TOOL,
    READ_FILE_TOOL,
    READ_MULTIPLE_FILES_TOOL,
    STAT_PATH_TOOL,
    WRITE_FILE_TOOL,
)


def file_read_specs() -> list[ToolSpec]:
    """Return tool specs for file read operations."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=READ_FILE_TOOL,
                description=(
                    "Read a file as text. Required param: path. Optional partial reads "
                    "(see each param): line_start/line_end (1-based), offset/limit (bytes), "
                    "head/tail (N lines). Partial reads return JSON — line mode: "
                    "total_lines/returned_lines/truncated; byte mode (offset/limit): "
                    "total_bytes/returned_bytes/truncated — otherwise plain text. "
                    'Example: {"path": "ralph-workflow/README.md"}.'
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
                                "(default: 5000000). Ignored when partial-read params are used."
                            ),
                            "default": 5_000_000,
                        },
                    },
                    "required": ["path"],
                    "allOf": [
                        {"not": {"required": ["line_start", "offset"]}},
                        {"not": {"required": ["line_start", "limit"]}},
                        {"not": {"required": ["line_end", "offset"]}},
                        {"not": {"required": ["line_end", "limit"]}},
                        {"not": {"required": ["line_start", "head"]}},
                        {"not": {"required": ["line_start", "tail"]}},
                        {"not": {"required": ["line_end", "head"]}},
                        {"not": {"required": ["line_end", "tail"]}},
                        {"not": {"required": ["offset", "head"]}},
                        {"not": {"required": ["offset", "tail"]}},
                        {"not": {"required": ["limit", "head"]}},
                        {"not": {"required": ["limit", "tail"]}},
                    ],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
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
                    "existing files if they exist. Returns a success confirmation "
                    "with the number of bytes written. "
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
                required_capability=McpCapability.WORKSPACE_WRITE_ANY.value,
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
                required_capability=McpCapability.WORKSPACE_READ.value,
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
                required_capability=McpCapability.WORKSPACE_METADATA_READ.value,
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
                required_capability=McpCapability.WORKSPACE_READ.value,
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_list_allowed_roots",
        ),
    ]
