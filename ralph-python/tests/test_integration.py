"""Integration tests for the policy-driven pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ralph.pipeline.effects import CommitEffect, ExitSuccessEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.orchestrator import determine_next_effect
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState
from ralph.policy.loader import load_policy


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
        development_budget_remaining=2,
        review_budget_remaining=2,
    )


def _apply(
    state: PipelineState, event: str
) -> PipelineState:
    bundle = _load_default_bundle()
    next_state, _ = reduce(state, cast(PipelineEvent, event), bundle.pipeline)
    return next_state


def test_full_pipeline_transitions_from_planning_to_complete() -> None:
    bundle = _load_default_bundle()
    state = _make_initial_state()
    visited_phases = [state.phase]

    assert isinstance(determine_next_effect(state, bundle.pipeline, bundle.agents), PreparePromptEffect)

    state = _apply(state, PipelineEvent.AGENT_SUCCESS)
    visited_phases.append(state.phase)
    assert state.phase == "development"
    assert isinstance(determine_next_effect(state, bundle.pipeline, bundle.agents), PreparePromptEffect)

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
