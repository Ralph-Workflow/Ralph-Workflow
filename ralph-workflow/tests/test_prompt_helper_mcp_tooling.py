"""Integration test: prompt-helper session MCP tool registry.

Guards against silent regressions where the standalone session with
{WORKSPACE_READ, WORKSPACE_METADATA_READ, GIT_STATUS_READ, GIT_DIFF_READ, ARTIFACT_SUBMIT}
exposes tools that should not be present (coordinate, plan-draft tools, write_file, exec).
"""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.workspace.memory import MemoryWorkspace

# Tools that MUST be present in the prompt-helper session
_REQUIRED_TOOLS = {
    "read_file",
    "list_directory",
    "list_directory_recursive",
    "search_files",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "ralph_submit_artifact",
}

# Tools that MUST be ABSENT from the prompt-helper session
_FORBIDDEN_TOOLS = {
    "write_file",
    "exec",
    "coordinate",
    "ralph_submit_plan_section",
    "ralph_finalize_plan",
    "ralph_get_plan_draft",
    "ralph_discard_plan_draft",
}


def test_prompt_helper_session_exposes_correct_tools() -> None:
    """Prompt-helper session exposes required read and artifact-submit tools."""
    session = AgentSession(
        session_id="prompt-helper-agent",
        run_id="test-run",
        drain="standalone",
        capabilities={
            Capability.WORKSPACE_READ.value,
            Capability.WORKSPACE_METADATA_READ.value,
            Capability.GIT_STATUS_READ.value,
            Capability.GIT_DIFF_READ.value,
            Capability.ARTIFACT_SUBMIT.value,
        },
    )
    workspace = MemoryWorkspace()

    bridge = build_ralph_tool_registry(
        session, workspace, upstream_registry=None, mcp_config=None
    )
    tool_names = {defn.name for defn in bridge.list_definitions()}

    missing = _REQUIRED_TOOLS - tool_names
    assert not missing, f"Tools missing from prompt-helper registry: {sorted(missing)}"


def test_prompt_helper_session_excludes_forbidden_tools() -> None:
    """Prompt-helper session excludes coordinate, plan-draft, write, and exec tools."""
    session = AgentSession(
        session_id="prompt-helper-agent",
        run_id="test-run",
        drain="standalone",
        capabilities={
            Capability.WORKSPACE_READ.value,
            Capability.WORKSPACE_METADATA_READ.value,
            Capability.GIT_STATUS_READ.value,
            Capability.GIT_DIFF_READ.value,
            Capability.ARTIFACT_SUBMIT.value,
        },
    )
    workspace = MemoryWorkspace()

    bridge = build_ralph_tool_registry(
        session, workspace, upstream_registry=None, mcp_config=None
    )
    tool_names = {defn.name for defn in bridge.list_definitions()}

    present_forbidden = _FORBIDDEN_TOOLS & tool_names
    assert not present_forbidden, (
        f"Forbidden tools present in prompt-helper registry: {sorted(present_forbidden)}"
    )
