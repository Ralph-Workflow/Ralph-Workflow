"""Tool specs for directory listing operations."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
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
                    "Optional params: recursive (boolean, default false), "
                    "view ('raw'|'compact'|'ranked'|'outline', default 'raw'), "
                    "include_counts (bool), include_symbols (bool), "
                    "changed_only (bool), limit_children (integer), "
                    "use_index ('auto'|'always'|'never', default 'auto'). "
                    "Returns an array of entries, each with type ('file' or 'dir'), "
                    "name, and relative path. Indexed views add counts/symbols when "
                    "requested; raw+never preserve legacy behavior. "
                    'Example: {"path": "ralph-workflow/ralph", "view": "outline"}.'
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
                        "view": {
                            "type": "string",
                            "enum": ["raw", "compact", "ranked", "outline"],
                            "description": (
                                "Listing view. ``raw`` is the legacy shape; "
                                "``compact`` includes counts; ``ranked`` ranks by "
                                "symbol count; ``outline`` includes top-level "
                                "symbols/headings."
                            ),
                            "default": "raw",
                        },
                        "include_counts": {
                            "type": "boolean",
                            "description": "Include indexed file counts by language/kind.",
                            "default": False,
                        },
                        "include_symbols": {
                            "type": "boolean",
                            "description": "Include top-level symbols/headings per child.",
                            "default": False,
                        },
                        "changed_only": {
                            "type": "boolean",
                            "description": "Filter or prioritize changed/stale paths.",
                            "default": False,
                        },
                        "limit_children": {
                            "type": "integer",
                            "description": "Cap the number of entries returned.",
                            "default": 100,
                            "minimum": 1,
                        },
                        "use_index": {
                            "type": "string",
                            "enum": ["auto", "always", "never"],
                            "description": (
                                "Indexed selector. ``auto`` uses the index when "
                                "available and falls back to live listing; "
                                "``always`` fails closed when counts/symbols "
                                "cannot be supplied; ``never`` preserves legacy."
                            ),
                            "default": "auto",
                        },
                    },
                    "required": ["path"],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
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
                        "ranked": {
                            "type": "boolean",
                            "description": (
                                "Rank matched paths by deterministic Phase-1 "
                                "score components (path, role, generated-penalty; "
                                "symbol/graph disabled)."
                            ),
                            "default": False,
                        },
                        "role": {
                            "type": "string",
                            "enum": ["source", "test", "docs", "config", "generated", "any"],
                            "description": (
                                "Restrict matches by path role heuristic. "
                                "'source' keeps .py/.md/.json/.yaml/.toml; "
                                "'test' keeps tests/. The 'any' value disables "
                                "the filter."
                            ),
                            "default": "any",
                        },
                        "contains_symbol": {
                            "type": "string",
                            "description": (
                                "Phase 2 selector. Phase 1 returns structured "
                                "'disabled:phase2' but still returns live glob results."
                            ),
                        },
                        "changed_only": {
                            "type": "boolean",
                            "description": (
                                "Restrict matches to git-changed paths. Phase 1 "
                                "returns an empty list (no git signal)."
                            ),
                            "default": False,
                        },
                        "return_evidence_ids": {
                            "type": "boolean",
                            "description": (
                                "Attach evidence_id handles to matched paths."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["pattern", "path"],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
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
                        "use_index": {
                            "type": "string",
                            "enum": ["auto", "always", "never"],
                            "description": (
                                "Indexed search selector. 'auto' uses FTS5 for "
                                "eligible patterns and falls back to live grep; "
                                "'always' fails closed for non-eligible patterns; "
                                "'never' is the legacy live grep behavior."
                            ),
                            "default": "auto",
                        },
                        "rank_by": {
                            "type": "string",
                            "enum": ["match", "symbol", "graph", "changed", "hybrid"],
                            "description": (
                                "Ranking strategy. Phase 1 only differentiates "
                                "'match' from the others via the git-changed "
                                "signal; symbol/graph components are stubbed "
                                "with a 'disabled:phase2' reason."
                            ),
                            "default": "match",
                        },
                        "return_evidence_ids": {
                            "type": "boolean",
                            "description": (
                                "Attach evidence_id handles to each match so the "
                                "caller can resolve the exact span with "
                                "read_file(evidence_id=...)."
                            ),
                            "default": False,
                        },
                        "max_snippet_lines": {
                            "type": "integer",
                            "description": (
                                "Cap the snippet length per indexed match. "
                                "Default 8 lines; set 0 to disable."
                            ),
                            "default": 8,
                        },
                        "dedupe_by_symbol": {
                            "type": "boolean",
                            "description": (
                                "Collapse repeated hits inside the same chunk/"
                                "evidence span (no-op in Phase 1 because "
                                "symbol spans arrive in Phase 2)."
                            ),
                            "default": False,
                        },
                        "include_graph_context": {
                            "type": "boolean",
                            "description": (
                                "Include caller/importer/test hints in the "
                                "response. Phase 1 returns 'disabled:phase2' "
                                "because the structural graph is not built yet."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["pattern", "path"],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
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
                required_capability=McpCapability.WORKSPACE_READ.value,
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
                    "exclude_patterns (list of glob patterns to exclude), "
                    "view ('raw'|'compact'|'ranked'|'outline', default 'raw'), "
                    "include_counts (bool), include_symbols (bool), "
                    "changed_only (bool, default false), limit_children (integer), "
                    "use_index ('auto'|'always'|'never'). "
                    "Indexed views surface symbol counts and headings; raw + never "
                    "preserve the legacy tree shape. "
                    'Example: {"path": ".", "max_depth": 2, "view": "ranked"}.'
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
                        "view": {
                            "type": "string",
                            "enum": ["raw", "compact", "ranked", "outline"],
                            "description": (
                                "Tree view. ``raw`` is the legacy shape; ``compact`` "
                                "adds counts; ``ranked`` ranks by symbol count; "
                                "``outline`` includes headings/symbols."
                            ),
                            "default": "raw",
                        },
                        "include_counts": {
                            "type": "boolean",
                            "description": "Include indexed file/symbol counts per dir.",
                            "default": False,
                        },
                        "include_symbols": {
                            "type": "boolean",
                            "description": "Include top-level symbols/headings per entry.",
                            "default": False,
                        },
                        "changed_only": {
                            "type": "boolean",
                            "description": (
                                "Filter the tree to subtrees that contain at least one "
                                "dirty (mutated) descendant. ``use_index='never'`` "
                                "disables the filter. ``use_index='always'`` fails "
                                "closed with reason 'no_explore_index_handle' when no "
                                "index is attached."
                            ),
                            "default": False,
                        },
                        "limit_children": {
                            "type": "integer",
                            "description": "Cap entries per directory.",
                            "default": 100,
                            "minimum": 1,
                        },
                        "use_index": {
                            "type": "string",
                            "enum": ["auto", "always", "never"],
                            "description": (
                                "Indexed selector for outline/counts metadata."
                            ),
                            "default": "auto",
                        },
                    },
                    "required": ["path"],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
            ),
            module_name="ralph.mcp.tools.workspace",
            handler_name="handle_directory_tree",
        ),
    ]
