"""Regression tests for unified MCP contract ownership.

These tests pin the main drift seams that previously diverged across
startup preflight, lifecycle restart logic, and prompt-visible capability
defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.protocol.startup import _visible_mcp_tool_names_owned as startup_visible_tools
from ralph.mcp.server.lifecycle import _visible_mcp_tool_names_owned as lifecycle_visible_tools
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.prompts.template_variables import default_capability_identifiers_for_drain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


_AGENTS_POLICY = AgentsPolicy(
    agent_chains={
        "planning": AgentChainConfig(agents=["claude"], max_retries=1, retry_delay_ms=1),
        "development": AgentChainConfig(agents=["claude"], max_retries=1, retry_delay_ms=1),
        "review": AgentChainConfig(agents=["claude"], max_retries=1, retry_delay_ms=1),
        "commit": AgentChainConfig(agents=["claude"], max_retries=1, retry_delay_ms=1),
    },
    agent_drains={
        "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
        "development": AgentDrainConfig(chain="development", drain_class="development"),
        "review": AgentDrainConfig(chain="review", drain_class="review"),
        "commit": AgentDrainConfig(chain="commit", drain_class="commit"),
    },
)


def _session_for_drain(drain: str, workspace_path: Path) -> AgentSession:
    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain=drain,
        workspace_path=workspace_path,
        agents_policy=_AGENTS_POLICY,
    )
    return AgentSession(
        session_id=f"{drain}-session",
        run_id=f"{drain}-run",
        drain=drain,
        capabilities=set(plan.capabilities),
    )


def test_startup_and_lifecycle_share_identical_owned_tool_surface(tmp_path: Path) -> None:
    session = _session_for_drain("development", tmp_path)
    workspace = MemoryWorkspace()

    assert startup_visible_tools(session, workspace) == lifecycle_visible_tools(session, workspace)


def test_planning_prompt_defaults_match_runtime_session_capabilities(tmp_path: Path) -> None:
    runtime = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="planning",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert default_capability_identifiers_for_drain(SessionDrain.PLANNING) == set(
        runtime.capabilities
    )


def test_review_prompt_defaults_match_runtime_session_capabilities(tmp_path: Path) -> None:
    runtime = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="review",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert default_capability_identifiers_for_drain(SessionDrain.REVIEW) == set(
        runtime.capabilities
    )


def test_commit_prompt_defaults_match_runtime_session_capabilities(tmp_path: Path) -> None:
    runtime = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="commit",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert default_capability_identifiers_for_drain(SessionDrain.COMMIT) == set(
        runtime.capabilities
    )
