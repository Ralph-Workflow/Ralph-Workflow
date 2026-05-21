"""MCP tool registry visibility tests for analysis and planning drains.

Guards against silent regressions where capability defaults look right but the
runtime tool registry filters tools via a different code path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.prompts.template_variables import (
    default_caps_and_flags_for_drain,
    visible_mcp_tool_names,
)
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_ANALYSIS_REQUIRED_TOOLS = {
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
    "ralph_get_plan_draft",
    "declare_complete",
}

_ANALYSIS_FORBIDDEN_TOOLS = {
    "write_file",
    "ralph_submit_plan_section",
    "ralph_finalize_plan",
    "ralph_discard_plan_draft",
}

_PLANNING_REQUIRED_TOOLS = {
    "ralph_get_plan_draft",
    "ralph_submit_plan_section",
    "ralph_finalize_plan",
    "ralph_discard_plan_draft",
}


_DEFAULT_AGENTS_POLICY = AgentsPolicy(
    agent_chains={
        "planning": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=1000),
        "analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
        "development_analysis": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=500
        ),
        "review_analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
    },
    agent_drains={
        "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
        "analysis": AgentDrainConfig(chain="analysis", drain_class="analysis"),
        "development_analysis": AgentDrainConfig(
            chain="development_analysis", drain_class="analysis"
        ),
        "review_analysis": AgentDrainConfig(chain="review_analysis", drain_class="analysis"),
    },
)


def _tool_names_for_runtime_drain(drain: str, workspace_path: Path) -> set[str]:
    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain=drain,
        workspace_path=workspace_path,
        agents_policy=_DEFAULT_AGENTS_POLICY,
    )
    session = AgentSession(
        session_id=f"{drain}-session",
        run_id=f"{drain}-run",
        drain=drain,
        capabilities=set(plan.capabilities),
    )
    workspace = MemoryWorkspace()
    bridge = build_ralph_tool_registry(session, workspace, upstream_registry=None, mcp_config=None)
    return {definition.name for definition in bridge.list_definitions()}


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
        capabilities={cap.value for cap in caps},
    )
    workspace = MemoryWorkspace()

    bridge = build_ralph_tool_registry(session, workspace, upstream_registry=None, mcp_config=None)
    tool_names = {definition.name for definition in bridge.list_definitions()}

    missing = _ANALYSIS_REQUIRED_TOOLS - tool_names
    assert not missing, f"Tools missing from {drain.value} registry: {sorted(missing)}"

    present_forbidden = _ANALYSIS_FORBIDDEN_TOOLS & tool_names
    assert not present_forbidden, (
        f"Forbidden tools present in {drain.value} registry: {sorted(present_forbidden)}"
    )


def test_planning_runtime_registry_exposes_plan_resubmission_tools(tmp_path: Path) -> None:
    tool_names = _tool_names_for_runtime_drain("planning", tmp_path)

    missing = _PLANNING_REQUIRED_TOOLS - tool_names
    assert not missing, f"Planning runtime registry is missing tools: {sorted(missing)}"


def test_planning_prompt_defaults_match_runtime_plan_tool_visibility(tmp_path: Path) -> None:
    runtime_tool_names = _tool_names_for_runtime_drain("planning", tmp_path)
    prompt_capabilities, _ = default_caps_and_flags_for_drain(SessionDrain.PLANNING)
    prompt_tool_names = set(visible_mcp_tool_names(prompt_capabilities))

    assert prompt_tool_names & _PLANNING_REQUIRED_TOOLS == (
        runtime_tool_names & _PLANNING_REQUIRED_TOOLS
    )
