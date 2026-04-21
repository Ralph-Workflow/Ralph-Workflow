"""Focused orchestration-flow tests for the policy-driven pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    ExitSuccessEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.orchestrator import determine_next_effect
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState
from ralph.policy.loader import load_policy

_EXPECTED_RECOVERY_DETERMINE_CALLS = 2


def _load_default_bundle():
    defaults_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _make_initial_state() -> PipelineState:
    bundle = _load_default_bundle()
    return PipelineState(
        phase=bundle.pipeline.entry_phase,
        total_iterations=1,
        total_reviewer_passes=1,
        dev_chain=AgentChainState(
            agents=bundle.agents.agent_chains["development"].agents,
        ),
        rev_chain=AgentChainState(
            agents=bundle.agents.agent_chains["review"].agents,
        ),
        rebase=RebaseState(),
        commit=CommitState(),
        development_budget_remaining=1,
        review_budget_remaining=1,
    )


def _apply(state: PipelineState, event: str) -> PipelineState:
    bundle = _load_default_bundle()
    next_state, _ = reduce(state, cast("PipelineEvent", event), bundle.pipeline)
    return next_state


def test_full_pipeline_transitions_from_planning_to_complete() -> None:
    bundle = _load_default_bundle()
    state = _make_initial_state()
    visited_phases = [state.phase]

    assert isinstance(
        determine_next_effect(state, bundle.pipeline, bundle.agents), PreparePromptEffect
    )

    state = _apply(state, PipelineEvent.AGENT_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "development"
    assert isinstance(
        determine_next_effect(state, bundle.pipeline, bundle.agents), PreparePromptEffect
    )

    state = _apply(state, PipelineEvent.AGENT_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "development_analysis"
    effect = determine_next_effect(state, bundle.pipeline, bundle.agents)
    assert isinstance(effect, PreparePromptEffect)
    assert effect.phase == "development_analysis"

    state = _apply(state, PipelineEvent.ANALYSIS_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "development_commit"
    assert isinstance(
        determine_next_effect(state, bundle.pipeline, bundle.agents),
        (CommitEffect, PreparePromptEffect),
    )

    state = _apply(state, PipelineEvent.COMMIT_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "review"

    state = _apply(state, PipelineEvent.AGENT_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "review_analysis"

    state = _apply(state, PipelineEvent.ANALYSIS_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "review_commit"

    state = _apply(state, PipelineEvent.COMMIT_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "complete"
    assert determine_next_effect(state, bundle.pipeline, bundle.agents) == ExitSuccessEffect()
    assert visited_phases == [
        "planning",
        "development",
        "development_analysis",
        "development_commit",
        "review",
        "review_analysis",
        "review_commit",
        "complete",
    ]


def test_run_recovers_when_planner_does_not_submit_plan_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("# Test Prompt\n\nUnattended planning recovery.")

    state = PipelineState(
        phase="planning",
        planning_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
        total_iterations=1,
        total_reviewer_passes=1,
        rebase=RebaseState(),
        commit=CommitState(),
    )

    config = MagicMock()
    config.general.developer_iters = 1
    config.general.reviewer_reviews = 1
    config.general.verbosity = 0
    config.agent_chains = {}
    config.agent_drains = {}

    mock_scope = MagicMock()
    mock_scope.root = tmp_path
    mock_scope.allowed_roots = [tmp_path]
    mock_bundle = MagicMock()
    mock_phase_def = MagicMock()
    mock_phase_def.requires_commit = False
    mock_phase_def.drain = "planning"
    mock_bundle.pipeline.phases.get.return_value = mock_phase_def

    call_count = 0

    def stub_determine_effect(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            )
        return ExitSuccessEffect()

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: mock_scope)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _: mock_bundle)
    monkeypatch.setattr(runner_module, "AgentRegistry", MagicMock())
    monkeypatch.setattr(runner_module, "FsWorkspace", MagicMock())
    monkeypatch.setattr(
        runner_module,
        "_materialize_agent_prompt_if_needed",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        runner_module,
        "_materialize_prepared_prompt",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        runner_module,
        "_emit_phase_transition_if_changed",
        lambda *args, **kwargs: args[1],
    )
    monkeypatch.setattr(
        runner_module,
        "_call_determine_effect_from_policy",
        stub_determine_effect,
    )
    monkeypatch.setattr(
        runner_module,
        "_phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_FAILURE,
    )
    monkeypatch.setattr(
        runner_module,
        "_execute_effect",
        lambda _effect, _config, _workspace_scope: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    console_mock = MagicMock()
    monkeypatch.setattr(runner_module, "console", console_mock)

    result = runner_module.run(config, initial_state=state)

    assert result == 0
    assert call_count == _EXPECTED_RECOVERY_DETERMINE_CALLS
