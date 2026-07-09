"""Tool specs for the explore index MCP tools."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import RALPH_INDEX_STATUS_TOOL, RALPH_REINDEX_TOOL


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
                    "index_storage_bytes, gitignore_coverage. "
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
                    "('changed'|'full'). Optional: timeout_ms (default 5000), "
                    "path_scope (list of relative paths). Returns: job_status, "
                    "generation, changed_files, failed_files, parse_count, "
                    "dirty_paths_count, elapsed_seconds, error_summary. "
                    "Fail-closed for the job; fail-open for the agent. "
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
                            "description": "Per-call budget in milliseconds (positive).",
                            "default": 5000,
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
    ]
