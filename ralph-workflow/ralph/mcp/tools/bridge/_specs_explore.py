"""Tool specs for the explore index MCP tools."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    RALPH_GRAPH_TOOL,
    RALPH_INDEX_STATUS_TOOL,
    RALPH_REINDEX_TOOL,
)


def explore_specs() -> list[ToolSpec]:
    """Return tool specs for the explore index tools."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=RALPH_INDEX_STATUS_TOOL,
                description=(
                    "Report Ralph's indexed exploration index health and freshness. "
                    "No required params. Returns: enabled, index_exists, generation, "
                    "indexed_at, files_indexed, files_stale, last_job, capabilities, "
                    "graph_backend, dirty_paths_count, cold_index_required, "
                    "last_refresh_kind, is_stale, stale_paths_count, "
                    "index_storage_bytes, managed_ignore_rule_present. "
                    'Side-effect free when no handle is attached. '
                    'Example: {} returns the live index status.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {},
                },
                required_capability=McpCapability.WORKSPACE_METADATA_READ.value,
            ),
            module_name="ralph.mcp.explore.handlers",
            handler_name="handle_ralph_index_status",
        ),
        ToolSpec(
            metadata=_metadata(
                name=RALPH_REINDEX_TOOL,
                description=(
                    "Run a bounded Ralph indexed exploration reindex. Required: mode "
                    "('changed'|'full'). Optional: timeout_ms (1-60000, default 5000), "
                    "path_scope (list of relative paths). Returns: job_status, "
                    "generation, changed_files, failed_files, parse_count, "
                    "dirty_paths_count, elapsed_seconds, error_summary. "
                    "Fail-closed for the job; fail-open for the agent. "
                    "timeout_ms outside [1, 60000] is rejected. "
                    'Example: {"mode": "changed", "timeout_ms": 5000}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["changed", "full"],
                            "description": "Reindex mode.",
                            "default": "changed",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "description": (
                                "Per-call budget in milliseconds (1-60000, default 5000). "
                                "Out-of-range values are rejected."
                            ),
                            "default": 5000,
                            "minimum": 1,
                            "maximum": 60000,
                        },
                        "path_scope": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Limit reindex to the given relative paths. "
                                "Empty list = whole workspace."
                            ),
                        },
                    },
                    "required": ["mode"],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
            ),
            module_name="ralph.mcp.explore.handlers",
            handler_name="handle_ralph_reindex",
        ),
        ToolSpec(
            metadata=_metadata(
                name=RALPH_GRAPH_TOOL,
                description=(
                    "Bounded graph-native query over Ralph's indexed "
                    "exploration substrate. Required: query_type "
                    "('neighbors'|'path'|'impact'|'hubs'|'tests') and target. "
                    "Optional: target_b, relations, limit (1-100, default 25), "
                    "freshness ('required'|'prefer_fresh'|'allow_stale'), "
                    "direction ('out'|'in'|'both'), depth (max 6), "
                    "max_paths (max 10), change_kind "
                    "('rename'|'signature'|'behavior'|'delete'|'unknown'), "
                    "scope_path, role, timeout_ms (1-30000, default 5000), "
                    "cancel (bool). Returns: nodes, edges, paths, "
                    "impacted_files, suggested_tests, confidence, "
                    "provenance, evidence_ids, missing_data, "
                    "index_generation, is_stale, truncated, "
                    "cancelled, deadline_exceeded. "
                    "Fail-closed on deadline/cancel: bounded incomplete "
                    "result, no mutable work exposed. "
                    'Example: {"query_type": "neighbors", "target": '
                    '"ralph.mcp.explore.handlers.handle_ralph_reindex"}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": [
                                "neighbors",
                                "path",
                                "impact",
                                "hubs",
                                "tests",
                            ],
                            "description": "Graph query kind.",
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "Symbol id, qualified name, or path to query."
                            ),
                        },
                        "target_b": {
                            "type": "string",
                            "description": (
                                "Second endpoint for path queries."
                            ),
                        },
                        "relations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Relation allowlist. Defaults to the full "
                                "evidence-backed relation set."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Result cap (1-100, default 25).",
                            "default": 25,
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "freshness": {
                            "type": "string",
                            "enum": ["required", "prefer_fresh", "allow_stale"],
                            "description": "Freshness policy.",
                            "default": "prefer_fresh",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["out", "in", "both"],
                            "description": "Traversal direction for neighbors.",
                            "default": "both",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Max traversal depth (1-3 for neighbors, 1-6 for path).",
                            "default": 1,
                            "minimum": 1,
                            "maximum": 6,
                        },
                        "max_paths": {
                            "type": "integer",
                            "description": "Max paths returned by path query (1-10).",
                            "default": 3,
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "change_kind": {
                            "type": "string",
                            "enum": [
                                "rename",
                                "signature",
                                "behavior",
                                "delete",
                                "unknown",
                            ],
                            "description": "Impact mode.",
                            "default": "unknown",
                        },
                        "scope_path": {
                            "type": "string",
                            "description": "Limit hubs to paths matching this prefix.",
                        },
                        "role": {
                            "type": "string",
                            "enum": ["source", "test", "docs", "config", "generated", "any"],
                            "description": "Optional role filter for hubs.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "description": (
                                "Per-call budget in milliseconds (1-30000, "
                                "default 5000). Fail-closed on deadline."
                            ),
                            "default": 5000,
                            "minimum": 1,
                            "maximum": 30000,
                        },
                        "cancel": {
                            "type": "boolean",
                            "description": (
                                "Request cooperative cancellation. When true, "
                                "the query returns a bounded incomplete result."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["query_type"],
                },
                required_capability=McpCapability.WORKSPACE_READ.value,
            ),
            module_name="ralph.mcp.explore.handlers",
            handler_name="handle_ralph_graph",
        ),
    ]
