"""Tool specs for directory listing operations."""

from __future__ import annotations

from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    DIRECTORY_TREE_TOOL,
    GREP_FILES_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    LIST_DIRECTORY_TOOL,
    SEARCH_FILES_TOOL,
)


def file_list_specs() -> list[ToolSpec]:
    """Return tool specs for directory listing operations."""
    return [
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
                            "description": ("Search pattern (regex if regex=True, else literal)."),
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
    ]
