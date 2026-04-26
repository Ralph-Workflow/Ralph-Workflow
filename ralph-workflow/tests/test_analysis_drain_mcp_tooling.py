"""Integration test: analysis drain MCP tool registry end-to-end.

Guards against silent regressions where capability defaults look right but the
tool registry filters tools via a different code path.
"""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.prompts.template_variables import default_caps_and_flags_for_drain
from ralph.workspace.memory import MemoryWorkspace

_REQUIRED_TOOLS = {
    "read_file",
    "list_directory",
    "list_directory_recursive",
    "directory_tree",
    "search_files",
    "git_diff",
    "git_status",
    "git_log",
    "git_show",
    "exec",
    "ralph_submit_artifact",
    "declare_complete",
    "coordinate",
}

_FORBIDDEN_TOOLS = {"write_file"}


@pytest.mark.parametrize(
    "drain",
    [SessionDrain.DEVELOPMENT_ANALYSIS, SessionDrain.REVIEW_ANALYSIS],
    ids=["development_analysis", "review_analysis"],
)
def test_analysis_drain_tool_registry_exposes_read_exec_and_artifact_tools(
    drain: SessionDrain,
) -> None:
    caps, _ = default_caps_and_flags_for_drain(drain)
    session = AgentSession(
        session_id="test-session",
        run_id="test-run",
        drain=drain.value,
        capabilities={c.value for c in caps},
    )
    workspace = MemoryWorkspace()

    bridge = build_ralph_tool_registry(session, workspace, upstream_registry=None, mcp_config=None)
    tool_names = {defn.name for defn in bridge.list_definitions()}

    missing = _REQUIRED_TOOLS - tool_names
    assert not missing, f"Tools missing from {drain.value} registry: {sorted(missing)}"

    present_forbidden = _FORBIDDEN_TOOLS & tool_names
    assert not present_forbidden, (
        f"Forbidden tools present in {drain.value} registry: {sorted(present_forbidden)}"
    )
