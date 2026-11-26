from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.session_plan import build_session_mcp_plan, default_prompt_capability_identifiers
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_DIFF_CAPABILITIES = frozenset({"git.diff_read", "git.status_read"})


@pytest.mark.parametrize(
    "drain",
    [SessionDrain.DEVELOPMENT_ANALYSIS, SessionDrain.ANALYSIS, SessionDrain.REVIEW_ANALYSIS],
)
def test_analysis_phases_are_never_granted_diff_or_status_tools(drain: SessionDrain) -> None:
    assert not default_prompt_capability_identifiers(drain) & _DIFF_CAPABILITIES


@pytest.mark.parametrize(
    "drain", [SessionDrain.DEVELOPMENT, SessionDrain.COMMIT, SessionDrain.DEVELOPMENT_COMMIT]
)
def test_phases_that_work_with_pending_changes_keep_diff_tools(drain: SessionDrain) -> None:
    assert default_prompt_capability_identifiers(drain) >= _DIFF_CAPABILITIES


def test_the_live_analysis_session_gets_no_diff_tools(tmp_path: Path) -> None:
    agents = AgentsPolicy(
        agent_chains={"development_analysis": AgentChainConfig(agents=["claude"])},
        agent_drains={
            "development_analysis": AgentDrainConfig(
                chain="development_analysis", drain_class="analysis"
            )
        },
    )
    plan = build_session_mcp_plan(
        transport=None,
        drain="development_analysis",
        workspace_path=tmp_path,
        agents_policy=agents,
    )

    assert not plan.capabilities & _DIFF_CAPABILITIES


def _rendered_analysis_prompt(tmp_path: Path) -> str:
    policy = PipelinePolicy(
        phases={
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                prompt_template="development_analysis.jinja",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="development_analysis",
        terminal_phase="complete",
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "development_analysis_decision": ArtifactContract(
                drain="development_analysis",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["approve", "request_changes"],
            )
        }
    )
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")
    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT_ANALYSIS),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=artifacts, previous_phase="development"),
    )
    return workspace.read(prompt_path)


def test_the_analysis_prompt_says_the_work_is_committed_and_the_tree_is_clean(
    tmp_path: Path,
) -> None:
    rendered = _rendered_analysis_prompt(tmp_path)

    assert "already committed" in rendered
    assert "never look for a diff" in rendered
    assert "inspect the current worktree" not in rendered
    assert "whole-change review" not in rendered
    assert "GIT: " not in rendered
    assert "git.diff_read" not in rendered
