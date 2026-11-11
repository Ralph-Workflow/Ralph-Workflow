
from __future__ import annotations

_GLOBAL_CONFIG_FILENAME = "ralph-workflow.toml"
_GLOBAL_AGENTS_FILENAME = "ralph-workflow-agents.toml"
_GLOBAL_MCP_FILENAME = "ralph-workflow-mcp.toml"
_GLOBAL_PIPELINE_FILENAME = "ralph-workflow-pipeline.toml"
_GLOBAL_ARTIFACTS_FILENAME = "ralph-workflow-artifacts.toml"
_LOCAL_CONFIG_FILENAME = "ralph-workflow.toml"
_LOCAL_MCP_FILENAME = "mcp.toml"
_LOCAL_POLICY_FILENAMES = ("pipeline.toml", "artifacts.toml")
_GLOBAL_POLICY_FILENAME_MAP = {
    "pipeline.toml": _GLOBAL_PIPELINE_FILENAME,
    "artifacts.toml": _GLOBAL_ARTIFACTS_FILENAME,
}
_ADVANCED_LOCAL_POLICY_FILENAMES = ("agents.toml",)
_LOCAL_CONFIG_SOURCE = "ralph-workflow-local.toml"

__all__ = [
    "_ADVANCED_LOCAL_POLICY_FILENAMES",
    "_GLOBAL_AGENTS_FILENAME",
    "_GLOBAL_ARTIFACTS_FILENAME",
    "_GLOBAL_CONFIG_FILENAME",
    "_GLOBAL_MCP_FILENAME",
    "_GLOBAL_PIPELINE_FILENAME",
    "_GLOBAL_POLICY_FILENAME_MAP",
    "_LOCAL_CONFIG_FILENAME",
    "_LOCAL_CONFIG_SOURCE",
    "_LOCAL_MCP_FILENAME",
    "_LOCAL_POLICY_FILENAMES",
]
