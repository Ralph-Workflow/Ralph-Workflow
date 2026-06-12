"""Black-box unit tests for env-aware prompt resolution through the orchestrator.

The Pro contract adds a fourth argument to ``determine_next_effect``:
``workspace_scope``. When supplied, the emitted
:class:`InvokeAgentEffect` carries a ``prompt_file`` resolved through
:func:`ralph.pro_support.prompt.resolve_effective_prompt_path`, honouring
the ``PROMPT_PATH`` env var. When the argument is ``None`` the legacy
literal ``"PROMPT.md"`` is preserved so existing tests and callers are
unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.orchestrator import determine_next_effect
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_policies_with_commit_phase() -> tuple[AgentsPolicy, PipelinePolicy]:
    """Return a small (agents, pipeline) policy containing a commit phase.

    The commit phase is what trips the agent-invocation branch of the
    orchestrator (``_is_agent_invoked_for_phase`` looks at
    ``state.commit.agent_invoked`` only for ``role == "commit"``
    phases).
    """
    agents = AgentsPolicy(
        agent_chains={
            "commit": AgentChainConfig(agents=["claude"], max_retries=1),
        },
        agent_drains={
            "commit": AgentDrainConfig(chain="commit"),
        },
    )
    pipeline = PipelinePolicy(
        phases={
            "commit": PhaseDefinition(
                drain="commit",
                role="commit",
                transitions=PhaseTransition(on_success="complete"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="commit",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="commit",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="commit",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    return agents, pipeline


def _make_state(phase: str) -> PipelineState:
    """Construct a PipelineState where ``commit.agent_invoked`` is True.

    The orchestrator only emits ``InvokeAgentEffect`` for commit phases
    once ``state.commit.agent_invoked`` is True. We pre-set that flag
    so the test exercises the prompt-resolution branch directly.
    """
    state = PipelineState(phase=phase)
    return state.model_copy(
        update={"commit": state.commit.model_copy(update={"agent_invoked": True})}
    )


def test_determine_next_effect_uses_resolved_prompt_path_with_workspace_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a WorkspaceScope and PROMPT_PATH set, the prompt_file uses the resolved path."""
    custom_prompt = tmp_path / "custom_prompt.md"
    custom_prompt.write_text("# custom\n", encoding="utf-8")
    monkeypatch.setenv("PROMPT_PATH", str(custom_prompt))

    agents, pipeline = _make_policies_with_commit_phase()
    state = _make_state("commit")

    workspace_scope = WorkspaceScope(tmp_path)
    effect = determine_next_effect(
        state, pipeline, agents, workspace_scope=workspace_scope
    )
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.prompt_file == str(custom_prompt.resolve())


def test_determine_next_effect_uses_default_prompt_when_workspace_scope_none() -> None:
    """With ``workspace_scope=None`` the legacy literal ``"PROMPT.md"`` is preserved."""
    agents, pipeline = _make_policies_with_commit_phase()
    state = _make_state("commit")

    effect = determine_next_effect(state, pipeline, agents)
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.prompt_file == "PROMPT.md"


def test_determine_next_effect_default_when_scope_provided_but_no_env(
    tmp_path: Path,
) -> None:
    """With a WorkspaceScope but no PROMPT_PATH, fall back to <workspace>/PROMPT.md."""
    agents, pipeline = _make_policies_with_commit_phase()
    state = _make_state("commit")

    workspace_scope = WorkspaceScope(tmp_path)
    effect = determine_next_effect(
        state, pipeline, agents, workspace_scope=workspace_scope
    )
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.prompt_file == str((workspace_scope.root / "PROMPT.md").resolve())


def test_determine_next_effect_pro_prompt_relative_to_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative PROMPT_PATH is resolved against the workspace root."""
    custom = tmp_path / "extra" / "goal.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("# extra\n", encoding="utf-8")
    monkeypatch.setenv("PROMPT_PATH", "extra/goal.md")

    agents, pipeline = _make_policies_with_commit_phase()
    state = _make_state("commit")

    workspace_scope = WorkspaceScope(tmp_path)
    effect = determine_next_effect(
        state, pipeline, agents, workspace_scope=workspace_scope
    )
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.prompt_file == str(custom.resolve())
